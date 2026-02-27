import json
import boto3
import urllib.request
from datetime import date, timedelta, timezone, datetime

JST = timezone(timedelta(hours=9))

ssm = boto3.client("ssm")

def get_param(name: str, decrypt: bool = False) -> str:
    res = ssm.get_parameter(Name=name, WithDecryption=decrypt)
    return res["Parameter"]["Value"]

def get_no_collection_dates() -> set[date]:
    raw = get_param("/garbage-notify/no-collection-dates")
    return {date.fromisoformat(d) for d in json.loads(raw)}

GARBAGE_RULES = {
    "tuesday": [
        "🗑️ 一般・有害ごみ",
        "　└ 割れ物（陶器・ガラス等）も出せます🫙",
    ],
    "friday": [
        "🗑️ 一般・有害ごみ",
        "　└ 割れ物（陶器・ガラス等）も出せます🫙",
    ],
    "wednesday":     ["♻️ プラスチック製容器包装"],
    "thursday_odd":  ["👕 繊維類・ペットボトル"],
    "thursday_even": ["🥫 飲料びん・缶"],
    "friday_odd": [
        "📰 紙類・金属類",
        "　└ ダンボールも紙類として出せます📦",
    ],
}

def get_garbage_today(today: date, no_collection: set[date]) -> list[str]:
    if today in no_collection:
        return []

    weekday = today.weekday()
    week_num = (today.day - 1) // 7 + 1
    is_odd_week = week_num % 2 == 1

    result = []
    if weekday == 1:
        result += GARBAGE_RULES["tuesday"]
    elif weekday == 4:
        result += GARBAGE_RULES["friday"]
        if is_odd_week:
            result += GARBAGE_RULES["friday_odd"]
    elif weekday == 2:
        result += GARBAGE_RULES["wednesday"]
    elif weekday == 3:
        key = "thursday_odd" if is_odd_week else "thursday_even"
        result += GARBAGE_RULES[key]

    return result

def build_message(today: date, items: list[str], no_collection: set[date]) -> str | None:
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]
    date_str = f"{today.month}月{today.day}日（{weekday_ja}）"

    if today in no_collection:
        return f"📅 {date_str}\n年末年始のためごみ収集はありません🎍"

    if not items:
        return None

    lines = [f"🗓️ {date_str} のごみ収集"]
    lines += items
    lines.append("⏰ 朝8時30分までに出してください")

    tomorrow = today + timedelta(days=1)
    if tomorrow in no_collection:
        lines.append("\n⚠️ 明日から年末年始の収集休止期間です")

    return "\n".join(lines)

def send_line_message(message: str, token: str, group_id: str):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {
        "to": group_id,
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
    token         = get_param("/garbage-notify/line/channel-access-token", decrypt=True)
    group_id      = get_param("/garbage-notify/line/group-id", decrypt=True)
    no_collection = get_no_collection_dates()

    today   = datetime.now(JST).date()
    items   = get_garbage_today(today, no_collection)
    message = build_message(today, items, no_collection)

    if message is None:
        return {"statusCode": 200, "body": "no notification today"}

    status = send_line_message(message, token, group_id)
    return {"statusCode": status}
