#!/usr/bin/env bash
# Delete everything Lotwise created in your AWS account.
set -euo pipefail
STACK_NAME="${STACK_NAME:-lotwise}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
for b in $(aws cloudformation describe-stack-resources --stack-name "$STACK_NAME" --region "$REGION" \
  --query "StackResources[?ResourceType=='AWS::S3::Bucket'].PhysicalResourceId" --output text); do
  echo "Emptying s3://$b"; aws s3 rm "s3://$b" --recursive --region "$REGION"
done
sam delete --stack-name "$STACK_NAME" --region "$REGION" --no-prompts
