import base64
import hashlib
import hmac
import json
import logging
import os
import urllib.error
import urllib.request

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

_DATA_PATH = os.path.join(os.path.dirname(__file__), "garbage_data.json")
with open(_DATA_PATH, encoding="utf-8") as f:
    GARBAGE_DB: list[dict] = json.load(f)

# コールドスタート時に一度だけ SSM から読み込み、以降は再利用する
_SECRETS_CACHE: dict[str, str] = {}


def get_param(name: str, decrypt: bool = False) -> str:
    if name in _SECRETS_CACHE:
        return _SECRETS_CACHE[name]
    res = ssm.get_parameter(Name=name, WithDecryption=decrypt)
    value = res["Parameter"]["Value"]
    _SECRETS_CACHE[name] = value
    return value


def verify_signature(body: str, signature: str, channel_secret: str) -> bool:
    if not signature:
        return False
    mac = hmac.new(channel_secret.encode(), body.encode(), hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    return hmac.compare_digest(expected, signature)


def search_garbage(keyword: str) -> list[dict]:
    kw = keyword.strip().lower()
    seen_ids: set[int] = set()
    exact: list[dict] = []
    prefix: list[dict] = []
    partial: list[dict] = []
    for d in GARBAGE_DB:
        item = d["item"].lower()
        if item == kw:
            exact.append(d)
            seen_ids.add(id(d))
    for d in GARBAGE_DB:
        if id(d) in seen_ids:
            continue
        if d["item"].lower().startswith(kw):
            prefix.append(d)
            seen_ids.add(id(d))
    for d in GARBAGE_DB:
        if id(d) in seen_ids:
            continue
        if kw in d["item"].lower():
            partial.append(d)
            seen_ids.add(id(d))
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


def reply_line(reply_token: str, message: str, token: str) -> int:
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
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status
    except urllib.error.HTTPError as e:
        logger.error("LINE reply HTTPError status=%s body=%s", e.code, e.read().decode(errors="replace"))
        raise
    except urllib.error.URLError as e:
        logger.error("LINE reply URLError reason=%s", e.reason)
        raise


def _get_header(event: dict, name: str) -> str:
    headers = event.get("headers") or {}
    lowered = {k.lower(): v for k, v in headers.items() if isinstance(k, str)}
    return lowered.get(name.lower(), "")


def lambda_handler(event, context):
    raw_body = event.get("body") or ""
    signature = _get_header(event, "x-line-signature")

    channel_secret = get_param("/garbage-notify/line/channel-secret", decrypt=True)
    if not verify_signature(raw_body, signature, channel_secret):
        logger.warning("invalid LINE signature")
        return {"statusCode": 403, "body": "invalid signature"}

    try:
        body = json.loads(raw_body or "{}")
    except json.JSONDecodeError:
        logger.warning("invalid JSON body")
        return {"statusCode": 400, "body": "invalid json"}

    token = get_param("/garbage-notify/line/channel-access-token", decrypt=True)

    for line_event in body.get("events", []):
        try:
            if line_event.get("type") != "message":
                continue
            if line_event.get("message", {}).get("type") != "text":
                continue

            text = line_event["message"]["text"].strip()
            reply_token = line_event.get("replyToken", "")
            if not text or not reply_token:
                continue

            results = search_garbage(text)
            if not results:
                continue

            reply_line(reply_token, build_reply(results), token)
        except Exception:
            logger.exception("failed to process line event")
            # 個別イベント失敗で全体を落とさない

    return {"statusCode": 200}
