import json
import logging
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

JST = timezone(timedelta(hours=9))

ssm = boto3.client("ssm")

_DATA_PATH = os.path.join(os.path.dirname(__file__), "districts.json")
with open(_DATA_PATH, encoding="utf-8") as f:
    _DISTRICTS_DATA = json.load(f)
DISTRICTS: dict[str, dict] = _DISTRICTS_DATA["districts"]


def nth_weekday_in_month(d: date) -> int:
    """月内でその曜日が何回目かを返す（1..5）。"""
    return (d.day - 1) // 7 + 1


def get_items_for(today: date, schedule: dict[str, dict[str, list[dict]]]) -> list[dict]:
    """その日に収集される品目行（main/sub）のリストを返す。"""
    wd_key = str(today.weekday())
    if wd_key not in schedule:
        return []
    nth_key = str(nth_weekday_in_month(today))
    return schedule[wd_key].get(nth_key, [])


def build_message(
    today: date,
    rows: list[dict],
    no_collection: set[date],
) -> str | None:
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]
    date_str = f"{today.month}月{today.day}日（{weekday_ja}）"

    if today in no_collection:
        return f"📅 {date_str}\n年末年始のためごみ収集はありません🎍"

    if not rows:
        return None

    lines = [f"🗓️ {date_str} のごみ収集"]
    for row in rows:
        lines.append(row["main"])
        if row.get("sub"):
            lines.append(row["sub"])
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


def fetch_secrets() -> tuple[str, str, set[date], str]:
    names = [
        "/garbage-notify/line/channel-access-token",
        "/garbage-notify/line/group-id",
        "/garbage-notify/no-collection-dates",
        "/garbage-notify/district-id",
    ]
    res = ssm.get_parameters(Names=names, WithDecryption=True)
    found = {p["Name"]: p["Value"] for p in res["Parameters"]}
    missing = [n for n in names if n not in found]
    if missing:
        raise RuntimeError(f"missing SSM parameters: {missing}")
    token = found["/garbage-notify/line/channel-access-token"]
    group_id = found["/garbage-notify/line/group-id"]
    no_collection = {
        date.fromisoformat(d)
        for d in json.loads(found["/garbage-notify/no-collection-dates"])
    }
    district_id = found["/garbage-notify/district-id"].strip()
    return token, group_id, no_collection, district_id


def lambda_handler(event, context):
    token, group_id, no_collection, district_id = fetch_secrets()

    if district_id not in DISTRICTS:
        raise RuntimeError(f"unknown district_id: {district_id}")
    district = DISTRICTS[district_id]
    schedule = district["schedule"]

    today = datetime.now(JST).date()
    rows = get_items_for(today, schedule)
    message = build_message(today, rows, no_collection)

    if message is None:
        logger.info(
            "no notification today: %s (district=%s/%s)",
            today.isoformat(),
            district_id,
            district["name"],
        )
        return {"statusCode": 200, "body": "no notification today"}

    status = send_line_message(message, token, group_id)
    logger.info(
        "sent notification status=%s date=%s district=%s/%s",
        status,
        today.isoformat(),
        district_id,
        district["name"],
    )
    return {"statusCode": status}
