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

    const PAGE_THRESHOLD = 50;

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

    // ─── Lambda 0: Count Pages (router) ──────────────────────────
    const countPagesFn = new lambda.Function(this, "CountPagesFn", {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "count_pages.lambda_handler",
      code: codeAsset,
      memorySize: 256,
      timeout: cdk.Duration.seconds(30),
      environment: { PAGE_THRESHOLD: String(PAGE_THRESHOLD) },
    });
    bucket.grantRead(countPagesFn);

    // ─── Lambda 1: Single Lambda (small docs) ────────────────────
    const singleFn = new lambda.Function(this, "PageIndexFunction", {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "handler.lambda_handler",
      code: codeAsset,
      memorySize: 2048,
      timeout: cdk.Duration.minutes(15),
      environment: { ...commonEnv, WORKFLOW_ARN: "" },
    });
    bucket.grantReadWrite(singleFn);
    singleFn.addToRolePolicy(bedrockPolicy);

    // ─── Lambda 2: Parse & Structure (large docs) ────────────────
    const parseAndStructureFn = new lambda.Function(this, "ParseAndStructureFn", {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "parse_and_structure.lambda_handler",
      code: codeAsset,
      memorySize: 2048,
      timeout: cdk.Duration.minutes(15),
      environment: commonEnv,
    });
    bucket.grantReadWrite(parseAndStructureFn);
    parseAndStructureFn.addToRolePolicy(bedrockPolicy);

    // ─── Lambda 3: Summarize Node (Map item) ─────────────────────
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

    // ─── Lambda 4: Assemble ──────────────────────────────────────
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
    }).itemProcessor(summarizeTask);

    const expressLogGroup = new logs.LogGroup(this, "ExpressWorkflowLogs", {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const expressWorkflow = new sfn.StateMachine(this, "SummaryExpressWorkflow", {
      stateMachineType: sfn.StateMachineType.EXPRESS,
      definitionBody: sfn.DefinitionBody.fromChainable(summaryMap),
      timeout: cdk.Duration.minutes(5),
      logs: { destination: expressLogGroup, level: sfn.LogLevel.ERROR },
    });

    // ─── Standard Workflow: Adaptive Pipeline ─────────────────────

    // Step 0: Count pages
    const countPagesTask = new tasks.LambdaInvoke(this, "CountPages", {
      lambdaFunction: countPagesFn,
      resultPath: "$.routeResult",
      resultSelector: {
        "bucket.$": "$.Payload.bucket",
        "key.$": "$.Payload.key",
        "pageCount.$": "$.Payload.pageCount",
        "strategy.$": "$.Payload.strategy",
      },
    });

    // Path A: Single Lambda (small docs)
    const singleLambdaTask = new tasks.LambdaInvoke(this, "SingleLambdaProcess", {
      lambdaFunction: singleFn,
      payload: sfn.TaskInput.fromObject({
        bucket: sfn.JsonPath.stringAt("$.routeResult.bucket"),
        key: sfn.JsonPath.stringAt("$.routeResult.key"),
        _source: "stepfunctions-single",
      }),
    });

    // Path B: Step Functions pipeline (large docs)
    const parseTask = new tasks.LambdaInvoke(this, "ParseAndStructure", {
      lambdaFunction: parseAndStructureFn,
      payload: sfn.TaskInput.fromObject({
        bucket: sfn.JsonPath.stringAt("$.routeResult.bucket"),
        key: sfn.JsonPath.stringAt("$.routeResult.key"),
      }),
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

    const callExpressWf = new tasks.StepFunctionsStartExecution(this, "RunParallelSummaries", {
      stateMachine: expressWorkflow,
      integrationPattern: sfn.IntegrationPattern.RUN_JOB,
      input: sfn.TaskInput.fromObject({
        bucket: sfn.JsonPath.stringAt("$.parseResult.bucket"),
        tmpPrefix: sfn.JsonPath.stringAt("$.parseResult.tmpPrefix"),
        nodes: sfn.JsonPath.listAt("$.parseResult.nodes"),
      }),
      resultPath: "$.summaryResult",
      resultSelector: { "summaries.$": "$.Output.summaries" },
    });

    const assembleTask = new tasks.LambdaInvoke(this, "Assemble", {
      lambdaFunction: assembleFn,
      payload: sfn.TaskInput.fromObject({
        bucket: sfn.JsonPath.stringAt("$.parseResult.bucket"),
        sourceKey: sfn.JsonPath.stringAt("$.parseResult.sourceKey"),
        docName: sfn.JsonPath.stringAt("$.parseResult.docName"),
        tmpPrefix: sfn.JsonPath.stringAt("$.parseResult.tmpPrefix"),
        summaries: sfn.JsonPath.listAt("$.summaryResult.summaries"),
      }),
    });

    const sfnPipeline = parseTask.next(callExpressWf).next(assembleTask);

    // Choice: route based on page count
    const routeChoice = new sfn.Choice(this, "ChooseStrategy")
      .when(sfn.Condition.stringEquals("$.routeResult.strategy", "single"), singleLambdaTask)
      .otherwise(sfnPipeline);

    const standardLogGroup = new logs.LogGroup(this, "StandardWorkflowLogs", {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const standardWorkflow = new sfn.StateMachine(this, "PageIndexWorkflow", {
      stateMachineType: sfn.StateMachineType.STANDARD,
      definitionBody: sfn.DefinitionBody.fromChainable(
        countPagesTask.next(routeChoice)
      ),
      timeout: cdk.Duration.minutes(30),
      logs: { destination: standardLogGroup, level: sfn.LogLevel.ALL },
    });

    // Wire up single Lambda → Standard Workflow (avoid circular dep)
    // Cannot use standardWorkflow.grantStartExecution(singleFn) because
    // singleFn is referenced in the workflow definition → circular dependency.
    singleFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["states:StartExecution", "states:DescribeExecution"],
        resources: [
          `arn:aws:states:*:${this.account}:stateMachine:*`,
          `arn:aws:states:*:${this.account}:execution:*:*`,
          `arn:aws:states:*:${this.account}:express:*:*:*`,
        ],
      })
    );

    // ─── Outputs ──────────────────────────────────────────────────
    new cdk.CfnOutput(this, "BucketName", { value: bucket.bucketName });
    new cdk.CfnOutput(this, "FunctionName", { value: singleFn.functionName });
    new cdk.CfnOutput(this, "WorkflowArn", { value: standardWorkflow.stateMachineArn });
    new cdk.CfnOutput(this, "PageThreshold", { value: String(PAGE_THRESHOLD) });

    // ─── Test Role Permissions ────────────────────────────────────
    const testRoleArn = this.node.tryGetContext("testRoleArn") as string | undefined;
    if (testRoleArn) {
      const principal = new iam.ArnPrincipal(testRoleArn);
      bucket.grantReadWrite(principal);
      singleFn.grantInvoke(principal);
      standardWorkflow.grantStartExecution(principal);
      standardWorkflow.grantRead(principal);
    }
  }
}
