#!/usr/bin/env bash
# Deploy Lotwise to your AWS account.
#
#   BEDROCK_MODEL_ID="<inference profile id from the Bedrock console>" ./deploy.sh
#
# Optional: STACK_NAME (default lotwise), AWS_REGION (default us-east-1).
# Safe to re-run: it updates the existing stack and re-uploads the website.
set -euo pipefail

STACK_NAME="${STACK_NAME:-lotwise}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
MODEL_ID="${BEDROCK_MODEL_ID:-}"
ROOT="$(cd "$(dirname "$0")" && pwd)"

for cmd in aws sam node npm curl; do
  command -v "$cmd" >/dev/null || { echo "Missing '$cmd'. See README.md > Prerequisites."; exit 1; }
done

echo "==> AWS identity"
aws sts get-caller-identity --output table

if [ -z "$MODEL_ID" ]; then
  echo "!!  BEDROCK_MODEL_ID is not set. The app will work, but column mapping will use keywords instead of AI."
fi

echo "==> Deploying backend stack '$STACK_NAME' to $REGION"
cd "$ROOT"
find backend/src -name "__pycache__" -type d -prune -exec rm -rf {} +
sam deploy \
  --template-file template.yaml \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides "BedrockModelId=${MODEL_ID}"

out() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
API_URL="$(out ApiUrl)"
WEB_URL="$(out WebsiteUrl)"
WEB_BUCKET="$(out WebBucketName)"

echo "==> Building website"
cd "$ROOT/frontend"
npm ci --no-audit --no-fund
npm run build
printf '{ "apiUrl": "%s" }\n' "$API_URL" > dist/config.json

echo "==> Uploading website"
aws s3 sync dist "s3://$WEB_BUCKET" --delete --region "$REGION" --exclude index.html --exclude config.json \
  --cache-control "public, max-age=31536000, immutable"
aws s3 cp dist/index.html "s3://$WEB_BUCKET/index.html" --region "$REGION" --cache-control "no-cache"
aws s3 cp dist/config.json "s3://$WEB_BUCKET/config.json" --region "$REGION" --cache-control "no-cache"
aws s3 sync dist/samples "s3://$WEB_BUCKET/samples" --region "$REGION" --cache-control "no-cache"

echo "==> Checking the API"
if curl -fsS "$API_URL/health" >/dev/null; then echo "API is healthy."; else echo "!!  API health check failed. Check CloudWatch logs for the ApiFunction."; fi

cat <<MSG

Lotwise is live:
  Website: $WEB_URL
  API:     $API_URL

Submit the Website URL to the hackathon.
MSG
