import * as cdk from "aws-cdk-lib/core";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3n from "aws-cdk-lib/aws-s3-notifications";
import { Construct } from "constructs";
import * as path from "path";

export class PageindexAwsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const bucket = new s3.Bucket(this, "DocumentBucket", {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const fn = new lambda.DockerImageFunction(this, "PageIndexFunction", {
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, "../lambda")
      ),
      memorySize: 2048,
      timeout: cdk.Duration.minutes(15),
      environment: {
        PAGEINDEX_MODEL:
          "bedrock/us.anthropic.claude-sonnet-4-6-20250929-v1:0",
        OUTPUT_PREFIX: "indexes/",
      },
      architecture: lambda.Architecture.X86_64,
    });

    bucket.grantReadWrite(fn);

    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: ["arn:aws:bedrock:*::foundation-model/*"],
      })
    );

    bucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(fn),
      { prefix: "uploads/", suffix: ".pdf" }
    );

    new cdk.CfnOutput(this, "BucketName", { value: bucket.bucketName });
    new cdk.CfnOutput(this, "FunctionName", { value: fn.functionName });
  }
}
