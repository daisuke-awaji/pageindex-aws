import * as cdk from "aws-cdk-lib/core";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3n from "aws-cdk-lib/aws-s3-notifications";
import { Construct } from "constructs";
import * as path from "path";
import { execSync } from "child_process";

export class PageindexAwsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const bucket = new s3.Bucket(this, "DocumentBucket", {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const lambdaDir = path.join(__dirname, "../lambda");
    const outputDir = path.join(__dirname, "../lambda/.build");

    const fn = new lambda.Function(this, "PageIndexFunction", {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "handler.lambda_handler",
      code: lambda.Code.fromAsset(lambdaDir, {
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
      }),
      memorySize: 2048,
      timeout: cdk.Duration.minutes(15),
      environment: {
        PAGEINDEX_MODEL:
          "bedrock/jp.anthropic.claude-haiku-4-5-20251001-v1:0",
        OUTPUT_PREFIX: "indexes/",
        BUCKET_NAME: bucket.bucketName,
      },
    });

    bucket.grantReadWrite(fn);

    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ],
        resources: [
          "arn:aws:bedrock:*::foundation-model/*",
          `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
        ],
      })
    );

    bucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(fn),
      { prefix: "uploads/", suffix: ".pdf" }
    );

    new cdk.CfnOutput(this, "BucketName", { value: bucket.bucketName });
    new cdk.CfnOutput(this, "FunctionName", { value: fn.functionName });

    // Allow a specific IAM role to upload test PDFs and invoke Lambda
    const testRoleArn = this.node.tryGetContext("testRoleArn") as
      | string
      | undefined;
    if (testRoleArn) {
      const principal = new iam.ArnPrincipal(testRoleArn);
      bucket.grantReadWrite(principal);
      fn.grantInvoke(principal);
    }
  }
}

