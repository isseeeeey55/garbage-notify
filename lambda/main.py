import json
import logging
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

JST = timezone(timedelta(hours=9))

ssm = boto3.client("ssm")


def get_no_collection_dates(raw: str) -> set[date]:
    return {date.fromisoformat(d) for d in json.loads(raw)}


# 川口市の収集カレンダー（地区固有）
# NOTE: "_odd" / "_even" は「その月でその曜日が何回目か」の奇偶を指す（ISO週番号ではない）。
#       自治体のカレンダーが「月内の第N木曜」で定義されている場合に一致する。
#       地区を変更した場合は該当地区のカレンダーと突き合わせて要再検証。
GARBAGE_RULES: dict[str, tuple[str, ...]] = {
    "tuesday": (
        "🗑️ 一般・有害ごみ",
        "　└ 割れ物（陶器・ガラス等）も出せます🫙",
    ),
    "friday": (
        "🗑️ 一般・有害ごみ",
        "　└ 割れ物（陶器・ガラス等）も出せます🫙",
    ),
    "wednesday":     ("♻️ プラスチック製容器包装",),
    "thursday_odd":  ("👕 繊維類・ペットボトル",),
    "thursday_even": ("🥫 飲料びん・缶",),
    "friday_odd": (
        "📰 紙類・金属類",
        "　└ ダンボールも紙類として出せます📦",
    ),
}


def nth_weekday_in_month(d: date) -> int:
    """その月で同じ曜日が何回目かを返す（1..5）。例: 2026-03-12(木) → 2"""
    return (d.day - 1) // 7 + 1


def get_garbage_today(today: date, no_collection: set[date]) -> list[str]:
    if today in no_collection:
        return []

    weekday = today.weekday()
    is_odd_week = nth_weekday_in_month(today) % 2 == 1

    result: list[str] = []
    if weekday == 1:  # 火
        result.extend(GARBAGE_RULES["tuesday"])
    elif weekday == 2:  # 水
        result.extend(GARBAGE_RULES["wednesday"])
    elif weekday == 3:  # 木
        key = "thursday_odd" if is_odd_week else "thursday_even"
        result.extend(GARBAGE_RULES[key])
    elif weekday == 4:  # 金
        result.extend(GARBAGE_RULES["friday"])
        if is_odd_week:
            result.extend(GARBAGE_RULES["friday_odd"])

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


def send_line_message(message: str, token: str, group_id: str) -> int:
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
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status
    except urllib.error.HTTPError as e:
        logger.error("LINE push HTTPError status=%s body=%s", e.code, e.read().decode(errors="replace"))
        raise
    except urllib.error.URLError as e:
        logger.error("LINE push URLError reason=%s", e.reason)
        raise


def fetch_secrets() -> tuple[str, str, set[date]]:
    # SecureString と String が混在するが、WithDecryption=True なら両方正しく取れる
    names = [
        "/garbage-notify/line/channel-access-token",
        "/garbage-notify/line/group-id",
        "/garbage-notify/no-collection-dates",
    ]
    res = ssm.get_parameters(Names=names, WithDecryption=True)
    found = {p["Name"]: p["Value"] for p in res["Parameters"]}
    missing = [n for n in names if n not in found]
    if missing:
        raise RuntimeError(f"missing SSM parameters: {missing}")
    token = found["/garbage-notify/line/channel-access-token"]
    group_id = found["/garbage-notify/line/group-id"]
    no_collection = get_no_collection_dates(found["/garbage-notify/no-collection-dates"])
    return token, group_id, no_collection


def lambda_handler(event, context):
    token, group_id, no_collection = fetch_secrets()

    today = datetime.now(JST).date()
    items = get_garbage_today(today, no_collection)
    message = build_message(today, items, no_collection)

    if message is None:
        logger.info("no notification today: %s", today.isoformat())
        return {"statusCode": 200, "body": "no notification today"}

    status = send_line_message(message, token, group_id)
    logger.info("sent notification status=%s date=%s", status, today.isoformat())
    return {"statusCode": status}
