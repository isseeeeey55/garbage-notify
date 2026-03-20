# garbage-notify

埼玉県川口市のごみ収集日をLINEに通知する AWS Lambda サービスです。毎朝7時にごみ収集スケジュールを自動通知するほか、LINEのトーク画面でごみ品目を検索すると分別方法を返答します。

## 機能

### 1. 毎朝通知 (`garbage-notify`)

EventBridge スケジュール（JST 7:00）で起動し、当日のごみ収集情報を LINE グループに Push 通知します。

| 曜日 | 収集品目 |
|------|----------|
| 火曜 | 一般・有害ごみ |
| 水曜 | プラスチック製容器包装 |
| 木曜（奇数週） | 繊維類・ペットボトル |
| 木曜（偶数週） | 飲料びん・缶 |
| 金曜 | 一般・有害ごみ |
| 金曜（奇数週） | 紙類・金属類 |

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
├── lambda/               # 毎朝通知 Lambda
│   └── main.py
├── lambda_webhook/       # ごみ分別検索 Webhook Lambda
│   ├── main.py
│   └── garbage_data.json # 分別データ
└── terraform/            # インフラ定義
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
```

### 2. Lambda パッケージをビルド

```bash
# 毎朝通知
cd lambda
zip lambda.zip main.py

# ごみ分別検索 Webhook
cd ../lambda_webhook
zip lambda_webhook.zip main.py garbage_data.json
```

### 3. Terraform でデプロイ

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

### 4. LINE Bot の Webhook URL を設定

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
