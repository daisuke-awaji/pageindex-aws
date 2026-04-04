    // Wire up single Lambda → Standard Workflow (avoid circular dep)
    // Cannot use standardWorkflow.grantStartExecution(singleFn) because
    // singleFn is used inside the workflow definition → circular dependency.
    // Instead, grant permissions via inline policy with wildcard ARN.
    singleFn.addEnvironment("WORKFLOW_ARN", standardWorkflow.stateMachineArn);
    singleFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["states:StartExecution", "states:DescribeExecution"],
        resources: ["*"],
      })
    );
