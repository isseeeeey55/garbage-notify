# garbage-notify

埼玉県川口市のごみ収集日を LINE に通知する AWS Lambda サービスです。毎朝7時に当日のごみ収集スケジュールを自動通知するほか、LINE のトーク画面でごみ品目を検索すると分別方法を返答します。

**川口市内のどの地区にも対応** しています。地区ごとの収集スケジュールは [kawaguchi-gomimaru.jp](https://kawaguchi-gomimaru.jp/) から取得した `districts.json`（135 地区分）を Lambda に同梱しており、SSM の `district-id` で地区を切り替えるだけで他地区にも使えます。

## 機能

### 1. 毎朝通知 (`garbage-notify`)

EventBridge スケジュール（JST 7:00）で起動し、当日のごみ収集情報を LINE グループに Push 通知します。

収集ルールは「**曜日 × 月内第 N 週（1〜5）**」のフルテーブルを `lambda/districts.json` に保持しており、川口市の「第 5 木曜は収集なし」「第 5 金曜は紙類・金属類の追加なし」などのエッジケースも正確に扱います。

年末年始などの収集休止日は SSM Parameter Store で管理し、その日は専用メッセージを送信します。

### 2. ごみ分別検索 Webhook (`garbage-search-webhook`)

LINE から送られたテキストメッセージをキーワードとして `garbage_data.json` を検索し、分別区分と出し方をリプライします。検索は完全一致 → 前方一致 → 部分一致の優先順位で最大5件返します。

## アーキテクチャ

```
【毎朝通知】
EventBridge (cron 0 22 * * ? * / JST 7:00)
  └─> Lambda: garbage-notify
        └─> SSM Parameter Store (トークン・グループID・休止日)
        └─> LINE Messaging API (Push)

【ごみ分別検索】
LINE Bot
  └─> API Gateway (HTTP API POST /webhook)
        └─> Lambda: garbage-search-webhook
              └─> garbage_data.json (分別データ)
              └─> SSM Parameter Store (トークン)
              └─> LINE Messaging API (Reply)
```

## ディレクトリ構成

```
garbage-notify/
├── lambda/                     # 毎朝通知 Lambda
│   ├── main.py
│   └── districts.json          # 川口市 135 地区の収集スケジュール
├── lambda_webhook/             # ごみ分別検索 Webhook Lambda
│   ├── main.py
│   └── garbage_data.json       # 分別データ
├── scripts/
│   └── build_districts.py      # districts.json 生成スクリプト
└── terraform/                  # インフラ定義
    ├── main.tf
    └── .terraform.lock.hcl
```

## セットアップ

### 前提条件

- AWS CLI（認証済み）
- Terraform >= 1.0
- Python 3.12

### 1. SSM Parameter Store にシークレットを登録

```bash
# LINE チャンネルアクセストークン（SecureString）
aws ssm put-parameter \
  --name "/garbage-notify/line/channel-access-token" \
  --value "<YOUR_LINE_CHANNEL_ACCESS_TOKEN>" \
  --type SecureString \
  --region ap-northeast-1

# LINE チャンネルシークレット（SecureString）Webhook 署名検証に必須
aws ssm put-parameter \
  --name "/garbage-notify/line/channel-secret" \
  --value "<YOUR_LINE_CHANNEL_SECRET>" \
  --type SecureString \
  --region ap-northeast-1

# LINE グループ ID（SecureString）
aws ssm put-parameter \
  --name "/garbage-notify/line/group-id" \
  --value "<YOUR_LINE_GROUP_ID>" \
  --type SecureString \
  --region ap-northeast-1

# 収集休止日リスト（String、JSON 配列形式）
aws ssm put-parameter \
  --name "/garbage-notify/no-collection-dates" \
  --value '["2025-01-01","2025-01-02","2025-01-03"]' \
  --type String \
  --region ap-northeast-1

# 地区 ID（String）kawaguchi-gomimaru.jp のカレンダー ID
# 例: 15 = 安行藤八。一覧は https://kawaguchi-gomimaru.jp/ で確認
aws ssm put-parameter \
  --name "/garbage-notify/district-id" \
  --value "15" \
  --type String \
  --region ap-northeast-1
```

### 2. 地区データの生成（初回・自治体ルール変更時のみ）

`scripts/build_districts.py` が kawaguchi-gomimaru.jp から 135 地区 × 3 ヶ月分のカレンダーを取得し、`lambda/districts.json` を生成します（所要時間 約 30 秒）。

```bash
python3 scripts/build_districts.py
```

このファイルはコミット済みなので、初回以外は実行不要です。

### 3. Lambda パッケージをビルド

```bash
# 毎朝通知
cd lambda
zip lambda.zip main.py districts.json

# ごみ分別検索 Webhook
cd ../lambda_webhook
zip lambda_webhook.zip main.py garbage_data.json
```

### 4. Terraform でデプロイ

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

デプロイ後、Webhook URL が出力されます。

```
webhook_url = "https://xxxxxxxxxx.execute-api.ap-northeast-1.amazonaws.com/webhook"
```

### 5. LINE Bot の Webhook URL を設定

[LINE Developers Console](https://developers.line.biz/) で Messaging API チャンネルの Webhook URL に上記の URL を設定してください。

## 技術スタック

| カテゴリ | 使用技術 |
|----------|----------|
| インフラ | Terraform |
| ランタイム | Python 3.12 |
| コンピュート | AWS Lambda |
| スケジューラ | Amazon EventBridge |
| API | Amazon API Gateway (HTTP API) |
| シークレット管理 | AWS Systems Manager Parameter Store |
| 通知 | LINE Messaging API |
