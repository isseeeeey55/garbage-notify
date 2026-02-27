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

resource "aws_iam_role_policy" "lambda_policy" {
  role = aws_iam_role.lambda_exec.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = [
          "arn:aws:ssm:ap-northeast-1:*:parameter/garbage-notify/line/channel-access-token",
          "arn:aws:ssm:ap-northeast-1:*:parameter/garbage-notify/line/group-id",
          "arn:aws:ssm:ap-northeast-1:*:parameter/garbage-notify/no-collection-dates",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_lambda_function" "garbage_notify" {
  function_name = "garbage-notify"
  runtime       = "python3.12"
  handler       = "main.lambda_handler"
  role          = aws_iam_role.lambda_exec.arn
  filename      = "../lambda/lambda.zip"
  timeout       = 30
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

resource "aws_lambda_function" "garbage_search" {
  function_name = "garbage-search-webhook"
  runtime       = "python3.12"
  handler       = "main.lambda_handler"
  role          = aws_iam_role.lambda_exec.arn
  filename      = "../lambda_webhook/lambda_webhook.zip"
  timeout       = 30
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
