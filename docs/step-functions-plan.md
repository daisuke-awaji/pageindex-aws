# PageIndex Step Functions 高速化計画

## 1. 現状分析: PageIndex 処理パイプライン

### 処理フロー（現行: 単一 Lambda）

```
┌─────────────────────────────────────────────────────────────────┐
│ Phase 1: 構造構築（逐次）                                         │
│  1. PDF Parse (CPU)                             ~1-3s           │
│  2. TOC Detection (逐次 LLM × 最大20ページ)       ~40-60s         │
│  3. TOC Transform / Structure Build (逐次 LLM)   ~10-30s         │
│  4. Title Verification (並列 asyncio.gather)      ~5-10s          │
│  5. Fix Incorrect TOC (並列 asyncio.gather)       ~0-10s          │
│  6. Large Node Recursive Processing (並列)        ~0-30s          │
├─────────────────────────────────────────────────────────────────┤
│ Phase 2: サマリ生成（並列可能・最大のボトルネック）                    │
│  7. add_node_text (CPU)                          ~1s             │
│  8. generate_summaries (並列 asyncio.gather)      ~10-60s ⚠️     │
│     → ノード数 × LLM呼び出し（全ノード独立）                        │
├─────────────────────────────────────────────────────────────────┤
│ Phase 3: 仕上げ                                                  │
│  9. generate_doc_description (LLM × 1)           ~3s             │
│ 10. Save to S3                                   ~1s             │
└─────────────────────────────────────────────────────────────────┘
```

### 並列化分析

| 処理 | 現状 | 並列化可能？ | アイテム数 | Step Functions 効果 |
|------|------|-------------|-----------|-------------------|
| TOC Detection | 逐次 | ✅ ページ独立 | 最大20 | 中 |
| TOC Transform | 逐次 | ❌ 依存関係 | 1-6回 | なし |
| Title Verification | asyncio.gather | ✅ | N items | 中 |
| **Summary Generation** | **asyncio.gather** | **✅ 完全独立** | **全ノード** | **🔥 最大** |
| Doc Description | 逐次 | ❌ サマリに依存 | 1回 | なし |

### 結論
**サマリ生成が最大の並列化ポイント**。100ノードの文書なら100回の独立LLM呼び出しを
Step Functions Map で真に並列実行できる。

---

## 2. 提案アーキテクチャ

### Standard Workflow + Express Workflow（ネスト）

```
S3 Event
  │
  ▼
Standard Workflow (大規模PDF対応、時間制限なし)
  │
  ├── Step 1: parse-and-structure (Lambda, 最大15分)
  │     - PDF解析 + テキスト抽出
  │     - TOC検出 + 構造構築
  │     - page_list を S3 に保存
  │     - ノードリスト（サマリ対象）を返却
  │     Output: { structureKey, nodes: [{nodeId, startIndex, endIndex}, ...] }
  │
  ├── Step 2: Express Workflow (Sync, ネスト実行)  ← 高速並列
  │     ┌─── Map State (maxConcurrency: 40) ──────────────┐
  │     │  Lambda "summarize-node" × N 並列                │
  │     │    - S3 から page_list を読み込み                   │
  │     │    - 対象ページのテキストを取得                       │
  │     │    - Bedrock Claude でサマリ生成                    │
  │     │    - Return: { nodeId, summary }                  │
  │     └──────────────────────────────────────────────────┘
  │     所要時間: ~5-15秒（ノード数に関係なく一定）
  │
  └── Step 3: assemble (Lambda)
        - 構造 + サマリをマージ
        - ドキュメント説明を生成（LLM × 1）
        - 最終 Index JSON を S3 に保存
```

### なぜ Express Workflow をネストするか？

| 観点 | Standard のみ | Standard + Express ネスト |
|------|--------------|------------------------|
| 並列サマリコスト | 状態遷移 ×N (高い) | Express は実行時間課金 (安い) |
| 大規模PDF対応 | ✅ 時間制限なし | ✅ Phase 1 は Standard |
| 並列サマリ速度 | Map で並列 | Map で並列 (同等) |
| Express 5分制限 | N/A | Phase 2 のみなので余裕 |

Express Workflow は**実行時間ベースの課金**で、状態遷移回数ベースの Standard より
高並列 Map 実行が安い。100ノードの Map なら：
- Standard: 100遷移 × $0.000025 = $0.0025
- Express: 実行時間 ≈ $0.00001（数秒の実行）

---

## 3. 処理時間の改善見積もり

### 50ページ PDF（約30ノード）の場合

| フェーズ | 現行（単一Lambda） | Step Functions |
|---------|------------------|----------------|
| 構造構築 | ~90秒 | ~90秒（変更なし） |
| サマリ生成 | ~60秒（asyncio） | **~5秒（Map並列）** |
| 仕上げ | ~5秒 | ~5秒 |
| **合計** | **~155秒** | **~100秒（35%短縮）** |

### 200ページ PDF（約100ノード）の場合

| フェーズ | 現行（単一Lambda） | Step Functions |
|---------|------------------|----------------|
| 構造構築 | ~5-10分 | ~5-10分（変更なし） |
| サマリ生成 | ~3-5分（asyncio） | **~10秒（Map並列）** |
| 仕上げ | ~5秒 | ~5秒 |
| **合計** | **~10-15分** | **~6-11分（30-40%短縮）** |

大規模ドキュメントほどサマリ生成の並列化効果が大きい。

---

## 4. Lambda 関数の分割設計

### Lambda 1: parse-and-structure
```
Input:  { bucket, key }
処理:   PDF解析 → TOC検出 → 構造構築 → Title検証
Output: {
  structureKey: "s3://bucket/tmp/{executionId}/structure.json",
  pageListKey:  "s3://bucket/tmp/{executionId}/pages.json",
  nodes: [
    { nodeId: "0001", startIndex: 3, endIndex: 5 },
    { nodeId: "0002", startIndex: 6, endIndex: 8 },
    ...
  ]
}
```

### Lambda 2: summarize-node
```
Input:  { pageListKey, nodeId, startIndex, endIndex, model }
処理:   S3からページテキスト読み込み → Bedrock でサマリ生成
Output: { nodeId: "0001", summary: "..." }
```
軽量（メモリ512MB、タイムアウト60秒で十分）

### Lambda 3: assemble
```
Input:  { structureKey, summaries: [...], bucket, outputKey }
処理:   構造 + サマリをマージ → ドキュメント説明生成 → S3保存
Output: { indexKey: "s3://bucket/indexes/xxx_structure.json" }
```

---

## 5. CDK 実装計画

### ファイル構成
```
pageindex-aws/
├── lambda/
│   ├── build.sh
│   ├── handler.py              ← 既存（S3イベント用、残す）
│   ├── parse_and_structure.py  ← NEW: Phase 1
│   ├── summarize_node.py       ← NEW: Phase 2 (Map item)
│   └── assemble.py             ← NEW: Phase 3
├── lib/
│   ├── pageindex-aws-stack.ts  ← 更新
│   └── step-functions-stack.ts ← NEW: Step Functions 定義
```

### Step Functions 定義（CDK）
```typescript
// Standard Workflow
const standardWorkflow = new sfn.StateMachine(this, "PageIndexWorkflow", {
  stateMachineType: sfn.StateMachineType.STANDARD,
  definition: /* chain */,
});

// Express Workflow (Summary parallelization)
const expressWorkflow = new sfn.StateMachine(this, "SummaryWorkflow", {
  stateMachineType: sfn.StateMachineType.EXPRESS,
  definition: new sfn.Map(this, "SummarizeNodes", {
    maxConcurrency: 40,
    itemsPath: "$.nodes",
  }).itemProcessor(
    new tasks.LambdaInvoke(this, "SummarizeNode", {
      lambdaFunction: summarizeNodeFn,
    })
  ),
});
```

### 実装ステップ

1. **Lambda 関数の分割実装**
   - `parse_and_structure.py`: 既存 handler.py の処理をPageIndex内部関数に分解
   - `summarize_node.py`: ページテキスト取得 + LLM サマリ生成
   - `assemble.py`: マージ + ドキュメント説明生成 + S3保存

2. **Express Workflow 定義（CDK）**
   - Map state でサマリ生成 Lambda を並列実行
   - maxConcurrency: 40（Bedrock のレートリミットを考慮）

3. **Standard Workflow 定義（CDK）**
   - Phase 1 → Express Workflow (Sync) → Phase 3 のチェーン
   - S3 Event からの起動設定

4. **デプロイ + テスト**
   - 小規模PDF（8ページ）で動作確認
   - 中規模PDF（30-50ページ）で速度比較

---

## 6. 考慮事項

### Bedrock レートリミット
- Claude Haiku 4.5 の TPM/RPM 制限に注意
- Map の maxConcurrency で調整（推奨: 20-40）
- 429 エラー時は Lambda 内でリトライ

### S3 中間データ
- 実行ごとに `/tmp/{executionId}/` に中間データを保存
- TTL で自動削除（S3 Lifecycle Rule）

### Step Functions ペイロードサイズ
- 最大 256KB（Standard）/ 256KB（Express）
- ページテキスト全体は S3 経由で受け渡し
- Map の入力はノードリスト（nodeId + startIndex + endIndex）のみ → 軽量

### フォールバック
- Express Workflow が失敗した場合、Standard Workflow の Catch で
  単一 Lambda にフォールバック（現行方式）
