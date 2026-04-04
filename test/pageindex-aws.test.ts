import * as cdk from "aws-cdk-lib/core";
import { Template, Match } from "aws-cdk-lib/assertions";
import { PageindexAwsStack } from "../lib/pageindex-aws-stack";

let template: Template;

beforeAll(() => {
  const app = new cdk.App();
  const stack = new PageindexAwsStack(app, "TestStack");
  template = Template.fromStack(stack);
});

describe("S3 Bucket", () => {
  test("bucket is created with auto-delete and lifecycle rule", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      LifecycleConfiguration: {
        Rules: Match.arrayWith([
          Match.objectLike({
            Prefix: "tmp/",
            ExpirationInDays: 1,
            Status: "Enabled",
          }),
        ]),
      },
    });
  });
});

describe("Lambda Functions", () => {
  test("5 Lambda functions are created", () => {
    template.resourceCountIs("AWS::Lambda::Function", 5 + 1); // +1 for auto-delete custom resource
  });

  test("CountPages Lambda has correct config", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "count_pages.lambda_handler",
      Runtime: "python3.12",
      MemorySize: 256,
      Timeout: 30,
    });
  });

  test("PageIndex single Lambda has 15min timeout", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "handler.lambda_handler",
      Timeout: 900,
      MemorySize: 2048,
    });
  });

  test("ParseAndStructure Lambda has 15min timeout", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "parse_and_structure.lambda_handler",
      Timeout: 900,
      MemorySize: 2048,
    });
  });

  test("SummarizeNode Lambda is lightweight", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "summarize_node.lambda_handler",
      MemorySize: 512,
      Timeout: 120,
    });
  });

  test("Assemble Lambda has 5min timeout", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "assemble.lambda_handler",
      Timeout: 300,
      MemorySize: 1024,
    });
  });

  test("all Lambda functions use ARM64", () => {
    const lambdas = template.findResources("AWS::Lambda::Function", {
      Properties: { Handler: Match.anyValue() },
    });
    for (const [id, resource] of Object.entries(lambdas)) {
      if (id.includes("AutoDeleteObjects")) continue;
      expect((resource as any).Properties.Architectures).toEqual(["arm64"]);
    }
  });
});

describe("Step Functions", () => {
  test("Standard Workflow is created", () => {
    template.hasResourceProperties("AWS::StepFunctions::StateMachine", {
      StateMachineType: "STANDARD",
    });
  });

  test("Express Workflow is created", () => {
    template.hasResourceProperties("AWS::StepFunctions::StateMachine", {
      StateMachineType: "EXPRESS",
    });
  });

  test("exactly 2 state machines", () => {
    template.resourceCountIs("AWS::StepFunctions::StateMachine", 2);
  });
});

describe("IAM Policies", () => {
  test("Bedrock InvokeModel permission exists", () => {
    template.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: Match.arrayWith(["bedrock:InvokeModel"]),
            Effect: "Allow",
          }),
        ]),
      },
    });
  });
});

describe("Outputs", () => {
  test("BucketName output exists", () => {
    template.hasOutput("BucketName", {});
  });

  test("WorkflowArn output exists", () => {
    template.hasOutput("WorkflowArn", {});
  });

  test("PageThreshold output exists", () => {
    template.hasOutput("PageThreshold", { Value: "50" });
  });
});

