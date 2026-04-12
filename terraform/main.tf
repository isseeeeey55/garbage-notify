provider "aws" {
  region = "ap-northeast-1"
}

resource "aws_iam_role" "lambda_exec" {
  name = "garbage-notify-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# SSM SecureString 用のデフォルト KMS キー（alias/aws/ssm）
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

resource "aws_iam_role_policy" "lambda_policy" {
  role = aws_iam_role.lambda_exec.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = [
          "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/garbage-notify/line/channel-access-token",
          "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/garbage-notify/line/channel-secret",
          "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/garbage-notify/line/group-id",
          "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/garbage-notify/no-collection-dates",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [data.aws_kms_alias.ssm.target_key_arn]
      },
      {
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "${aws_cloudwatch_log_group.garbage_notify.arn}:*",
          "${aws_cloudwatch_log_group.garbage_search.arn}:*",
        ]
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "garbage_notify" {
  name              = "/aws/lambda/garbage-notify"
  retention_in_days = 30
}

resource "aws_lambda_function" "garbage_notify" {
  function_name    = "garbage-notify"
  runtime          = "python3.12"
  handler          = "main.lambda_handler"
  role             = aws_iam_role.lambda_exec.arn
  filename         = "../lambda/lambda.zip"
  source_code_hash = filebase64sha256("../lambda/lambda.zip")
  timeout          = 30

  depends_on = [aws_cloudwatch_log_group.garbage_notify]
}

resource "aws_cloudwatch_event_rule" "daily" {
  name                = "garbage-notify-daily"
  schedule_expression = "cron(0 22 * * ? *)"
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule = aws_cloudwatch_event_rule.daily.name
  arn  = aws_lambda_function.garbage_notify.arn
}

resource "aws_lambda_permission" "eventbridge" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.garbage_notify.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily.arn
}

# ── ごみ分別検索 Webhook ──────────────────────────────────

resource "aws_cloudwatch_log_group" "garbage_search" {
  name              = "/aws/lambda/garbage-search-webhook"
  retention_in_days = 30
}

resource "aws_lambda_function" "garbage_search" {
  function_name    = "garbage-search-webhook"
  runtime          = "python3.12"
  handler          = "main.lambda_handler"
  role             = aws_iam_role.lambda_exec.arn
  filename         = "../lambda_webhook/lambda_webhook.zip"
  source_code_hash = filebase64sha256("../lambda_webhook/lambda_webhook.zip")
  timeout          = 30

  depends_on = [aws_cloudwatch_log_group.garbage_search]
}

resource "aws_cloudwatch_log_group" "webhook_api" {
  name              = "/aws/apigateway/garbage-search-webhook"
  retention_in_days = 30
}

resource "aws_apigatewayv2_api" "webhook" {
  name          = "garbage-search-webhook"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "webhook" {
  api_id                 = aws_apigatewayv2_api.webhook.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.garbage_search.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "webhook" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /webhook"
  target    = "integrations/${aws_apigatewayv2_integration.webhook.id}"
}

resource "aws_apigatewayv2_stage" "webhook" {
  api_id      = aws_apigatewayv2_api.webhook.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.webhook_api.arn
    format = jsonencode({
      requestId               = "$context.requestId"
      ip                      = "$context.identity.sourceIp"
      requestTime             = "$context.requestTime"
      httpMethod              = "$context.httpMethod"
      routeKey                = "$context.routeKey"
      status                  = "$context.status"
      protocol                = "$context.protocol"
      responseLength          = "$context.responseLength"
      integrationStatus       = "$context.integrationStatus"
      integrationErrorMessage = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "apigw_webhook" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.garbage_search.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}

output "webhook_url" {
  value = "${aws_apigatewayv2_stage.webhook.invoke_url}/webhook"
}
