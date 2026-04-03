import * as cdk from "aws-cdk-lib/core";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as logs from "aws-cdk-lib/aws-logs";
import { Construct } from "constructs";
import * as path from "path";
import { execSync } from "child_process";

export class PageindexAwsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ─── S3 Bucket ────────────────────────────────────────────────
    const bucket = new s3.Bucket(this, "DocumentBucket", {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      lifecycleRules: [
        { prefix: "tmp/", expiration: cdk.Duration.days(1) },
      ],
    });

    // ─── Shared Code Bundle ───────────────────────────────────────
    const lambdaDir = path.join(__dirname, "../lambda");
    const codeAsset = lambda.Code.fromAsset(lambdaDir, {
      bundling: {
        image: lambda.Runtime.PYTHON_3_12.bundlingImage,
        local: {
          tryBundle(outputDir: string): boolean {
            execSync(`bash ${lambdaDir}/build.sh ${outputDir}`, {
              stdio: "inherit",
            });
            return true;
          },
        },
      },
    });

    const MODEL = "bedrock/jp.anthropic.claude-haiku-4-5-20251001-v1:0";
    const commonEnv = {
      PAGEINDEX_MODEL: MODEL,
      OUTPUT_PREFIX: "indexes/",
      BUCKET_NAME: bucket.bucketName,
    };

    const bedrockPolicy = new iam.PolicyStatement({
      actions: [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
      ],
      resources: [
        "arn:aws:bedrock:*::foundation-model/*",
        `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
      ],
    });

    // ─── Lambda 1: Parse & Structure (Phase 1) ───────────────────
    const parseAndStructureFn = new lambda.Function(
      this,
      "ParseAndStructureFn",
      {
        runtime: lambda.Runtime.PYTHON_3_12,
        architecture: lambda.Architecture.ARM_64,
        handler: "parse_and_structure.lambda_handler",
        code: codeAsset,
        memorySize: 2048,
        timeout: cdk.Duration.minutes(15),
        environment: commonEnv,
      }
    );
    bucket.grantReadWrite(parseAndStructureFn);
    parseAndStructureFn.addToRolePolicy(bedrockPolicy);

    // ─── Lambda 2: Summarize Node (Phase 2 – Map item) ──────────
    const summarizeNodeFn = new lambda.Function(this, "SummarizeNodeFn", {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "summarize_node.lambda_handler",
      code: codeAsset,
      memorySize: 512,
      timeout: cdk.Duration.seconds(120),
      environment: commonEnv,
    });
    bucket.grantRead(summarizeNodeFn);
    summarizeNodeFn.addToRolePolicy(bedrockPolicy);

    // ─── Lambda 3: Assemble (Phase 3) ────────────────────────────
    const assembleFn = new lambda.Function(this, "AssembleFn", {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "assemble.lambda_handler",
      code: codeAsset,
      memorySize: 1024,
      timeout: cdk.Duration.minutes(5),
      environment: commonEnv,
    });
    bucket.grantReadWrite(assembleFn);
    assembleFn.addToRolePolicy(bedrockPolicy);

    // ─── Legacy single-Lambda (kept for S3 event trigger) ────────
    const singleFn = new lambda.Function(this, "PageIndexFunction", {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "handler.lambda_handler",
      code: codeAsset,
      memorySize: 2048,
      timeout: cdk.Duration.minutes(15),
      environment: commonEnv,
    });
    bucket.grantReadWrite(singleFn);
    singleFn.addToRolePolicy(bedrockPolicy);

    // ─── Express Workflow: Parallel Summary Generation ────────────
    const summarizeTask = new tasks.LambdaInvoke(this, "SummarizeNode", {
      lambdaFunction: summarizeNodeFn,
      resultSelector: {
        "nodeId.$": "$.Payload.nodeId",
        "summary.$": "$.Payload.summary",
      },
    });

    const summaryMap = new sfn.Map(this, "SummarizeAllNodes", {
      maxConcurrency: 40,
      itemsPath: "$.nodes",
      resultPath: "$.summaries",
      itemSelector: {
        "nodeId.$": "$.Map.Item.Value.nodeId",
        "title.$": "$.Map.Item.Value.title",
        "startIndex.$": "$.Map.Item.Value.startIndex",
        "endIndex.$": "$.Map.Item.Value.endIndex",
        "bucket.$": "$.bucket",
        "tmpPrefix.$": "$.tmpPrefix",
      },
    }).itemProcessor(summarizeTask);

    const expressLogGroup = new logs.LogGroup(this, "ExpressWorkflowLogs", {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const expressWorkflow = new sfn.StateMachine(
      this,
      "SummaryExpressWorkflow",
      {
        stateMachineType: sfn.StateMachineType.EXPRESS,
        definitionBody: sfn.DefinitionBody.fromChainable(summaryMap),
        timeout: cdk.Duration.minutes(5),
        logs: {
          destination: expressLogGroup,
          level: sfn.LogLevel.ERROR,
        },
      }
    );

    // ─── Standard Workflow: Full Pipeline ─────────────────────────
    const parseTask = new tasks.LambdaInvoke(this, "ParseAndStructure", {
      lambdaFunction: parseAndStructureFn,
      resultPath: "$.parseResult",
      resultSelector: {
        "bucket.$": "$.Payload.bucket",
        "sourceKey.$": "$.Payload.sourceKey",
        "docName.$": "$.Payload.docName",
        "tmpPrefix.$": "$.Payload.tmpPrefix",
        "nodeCount.$": "$.Payload.nodeCount",
        "nodes.$": "$.Payload.nodes",
      },
    });

    const callExpressWf = new tasks.StepFunctionsStartExecution(
      this,
      "RunParallelSummaries",
      {
        stateMachine: expressWorkflow,
        integrationPattern: sfn.IntegrationPattern.RUN_JOB,
        input: sfn.TaskInput.fromObject({
          bucket: sfn.JsonPath.stringAt("$.parseResult.bucket"),
          tmpPrefix: sfn.JsonPath.stringAt("$.parseResult.tmpPrefix"),
          nodes: sfn.JsonPath.listAt("$.parseResult.nodes"),
        }),
        resultPath: "$.summaryResult",
        resultSelector: {
          "summaries.$": "$.Output",
        },
      }
    );

    const assembleTask = new tasks.LambdaInvoke(this, "Assemble", {
      lambdaFunction: assembleFn,
      resultPath: "$.assembleResult",
      payload: sfn.TaskInput.fromObject({
        bucket: sfn.JsonPath.stringAt("$.parseResult.bucket"),
        sourceKey: sfn.JsonPath.stringAt("$.parseResult.sourceKey"),
        docName: sfn.JsonPath.stringAt("$.parseResult.docName"),
        tmpPrefix: sfn.JsonPath.stringAt("$.parseResult.tmpPrefix"),
        summaries: sfn.JsonPath.listAt("$.summaryResult.summaries"),
      }),
    });

    const standardLogGroup = new logs.LogGroup(this, "StandardWorkflowLogs", {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const standardWorkflow = new sfn.StateMachine(
      this,
      "PageIndexWorkflow",
      {
        stateMachineType: sfn.StateMachineType.STANDARD,
        definitionBody: sfn.DefinitionBody.fromChainable(
          parseTask.next(callExpressWf).next(assembleTask)
        ),
        timeout: cdk.Duration.minutes(30),
        logs: {
          destination: standardLogGroup,
          level: sfn.LogLevel.ALL,
        },
      }
    );

    // ─── Outputs ──────────────────────────────────────────────────
    new cdk.CfnOutput(this, "BucketName", { value: bucket.bucketName });
    new cdk.CfnOutput(this, "FunctionName", { value: singleFn.functionName });
    new cdk.CfnOutput(this, "WorkflowArn", {
      value: standardWorkflow.stateMachineArn,
    });
    new cdk.CfnOutput(this, "ExpressWorkflowArn", {
      value: expressWorkflow.stateMachineArn,
    });

    // ─── Test Role Permissions ────────────────────────────────────
    const testRoleArn = this.node.tryGetContext("testRoleArn") as
      | string
      | undefined;
    if (testRoleArn) {
      const principal = new iam.ArnPrincipal(testRoleArn);
      bucket.grantReadWrite(principal);
      singleFn.grantInvoke(principal);
      standardWorkflow.grantStartExecution(principal);
      standardWorkflow.grantRead(principal);
    }
  }
}

