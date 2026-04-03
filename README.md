# PageIndex on AWS

[PageIndex](https://github.com/VectifyAI/PageIndex) を AWS Lambda + S3 + Amazon Bedrock で動かす PoC。

## Architecture

```
S3 (uploads/*.pdf) → Lambda (PageIndex + Bedrock Claude) → S3 (indexes/*_structure.json)
```

- PDF を `uploads/` プレフィックスに置くと Lambda が自動起動
- PageIndex がツリー構造インデックスを生成し `indexes/` に保存

## Deploy

```bash
npx cdk deploy
```

## Usage

```bash
# デプロイ後の出力からバケット名を取得
BUCKET=$(aws cloudformation describe-stacks --stack-name PageindexAwsStack \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)

# PDF アップロード（インデキシング開始）
aws s3 cp your-document.pdf s3://$BUCKET/uploads/

# 数分後にインデックスを確認
aws s3 ls s3://$BUCKET/indexes/
aws s3 cp s3://$BUCKET/indexes/your-document_structure.json .
```

## Configuration

Lambda 環境変数:

| Key | Default | Description |
|-----|---------|-------------|
| `PAGEINDEX_MODEL` | `bedrock/us.anthropic.claude-sonnet-4-6-20250929-v1:0` | LiteLLM model identifier |
| `OUTPUT_PREFIX` | `indexes/` | S3 prefix for output index JSON |

