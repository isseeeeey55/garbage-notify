import json
import os
import boto3
import urllib.request

ssm = boto3.client("ssm")

# 起動時に一度だけデータを読み込む
_DATA_PATH = os.path.join(os.path.dirname(__file__), "garbage_data.json")
with open(_DATA_PATH, encoding="utf-8") as f:
    GARBAGE_DB: list[dict] = json.load(f)


def get_param(name: str, decrypt: bool = False) -> str:
    res = ssm.get_parameter(Name=name, WithDecryption=decrypt)
    return res["Parameter"]["Value"]


def search_garbage(keyword: str) -> list[dict]:
    kw = keyword.strip().lower()
    # 完全一致優先、次に前方一致、次に部分一致
    exact   = [d for d in GARBAGE_DB if d["item"].lower() == kw]
    prefix  = [d for d in GARBAGE_DB if d["item"].lower().startswith(kw) and d not in exact]
    partial = [d for d in GARBAGE_DB if kw in d["item"].lower() and d not in exact and d not in prefix]
    return (exact + prefix + partial)[:5]


def build_reply(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results):
        if i > 0:
            lines.append("")
        lines.append(f"【{r['item']}】")
        lines.append(f"分別：{r['category']}")
        if r["tip"]:
            lines.append(f"出し方：{r['tip']}")
    return "\n".join(lines)


def reply_line(reply_token: str, message: str, token: str):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": message}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req) as res:
        return res.status


def lambda_handler(event, context):
    body = json.loads(event.get("body") or "{}")

    for line_event in body.get("events", []):
        if line_event.get("type") != "message":
            continue
        if line_event.get("message", {}).get("type") != "text":
            continue

        text        = line_event["message"]["text"].strip()
        reply_token = line_event.get("replyToken", "")

        if not text:
            continue

        results = search_garbage(text)
        if not results:
            continue  # ヒットしなければ無視

        token   = get_param("/garbage-notify/line/channel-access-token", decrypt=True)
        message = build_reply(results)
        reply_line(reply_token, message, token)

    return {"statusCode": 200}
