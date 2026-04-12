"""川口市のごみ収集カレンダーを kawaguchi-gomimaru.jp からスクレイピングして
districts.json を生成するビルドスクリプト。

年 1 回、または自治体のルール変更時に手動で実行する。

出力の schedule は (weekday, nth_occurrence_in_month) → 表示行リストの完全テーブル。
odd/even ではなく第 N 週単位で管理するため、第 5 週で収集がない等のエッジケースも
正確に表現できる。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

BASE_URL = "https://kawaguchi-gomimaru.jp"
USER_AGENT = "garbage-notify-build/1.0 (+https://github.com/isseeeeey55/garbage-notify)"
MAX_WORKERS = 5
BATCH_SLEEP = 0.1  # 秒

# 収集グループ定義: (アンカー集合, 表示行 main, 表示行 sub)
# アンカーのどれか 1 つでも含まれていればこのグループが出力される。
COLLECTION_GROUPS: list[tuple[frozenset[str], str, str | None]] = [
    (
        frozenset({"ippangomi", "yugaigomi"}),
        "🗑️ 一般・有害ごみ",
        "　└ 割れ物（陶器・ガラス等）も出せます🫙",
    ),
    (frozenset({"pura"}), "♻️ プラスチック製容器包装", None),
    (frozenset({"petbottle", "seni"}), "👕 繊維類・ペットボトル", None),
    (frozenset({"bin", "kan", "inryoukan"}), "🥫 飲料びん・缶", None),
    (
        frozenset({"kinzokurui", "kamipakku"}),
        "📰 紙類・金属類",
        "　└ ダンボールも紙類として出せます📦",
    ),
]
KNOWN_ANCHORS = frozenset().union(*(g[0] for g in COLLECTION_GROUPS))


def fetch(url: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as res:
                return res.read().decode("utf-8")
        except (urllib.error.URLError, urllib.error.HTTPError):
            if attempt == retries - 1:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def fetch_district_list() -> list[tuple[int, str]]:
    html = fetch(f"{BASE_URL}/")
    pattern = re.compile(r"idx:'[^']+',id:'(\d+)',city:'([^']+)'")
    return [(int(i), name) for i, name in pattern.findall(html)]


_TD_RE = re.compile(
    r"<td[^>]*>\s*(\d{1,2})((?:<div class=\"p\d+ calendarTxt\">.*?</div>)*)\s*</td>",
    re.DOTALL,
)
_ANCHOR_RE = re.compile(r"href=\"[^\"]*#(\w+)\"")


def parse_calendar_html(html: str) -> dict[int, set[str]]:
    """HTML から {日付: {アンカー集合}} を抽出。アンカーなしの日も含める。"""
    start = html.find('class="calendarTable')
    if start < 0:
        return {}
    end = html.find("</table>", start)
    body = html[start:end] if end > 0 else html[start:]

    result: dict[int, set[str]] = {}
    for m in _TD_RE.finditer(body):
        day = int(m.group(1))
        if 1 <= day <= 31:
            result[day] = set(_ANCHOR_RE.findall(m.group(2)))
    return result


def fetch_district_calendar(
    district_id: int, year: int, months: list[int]
) -> dict[tuple[int, int], set[str]]:
    """{(month, day): anchors} を返す。アンカーが空の日も含める。"""
    out: dict[tuple[int, int], set[str]] = {}
    for m in months:
        url = f"{BASE_URL}/calendar/{district_id}/{year}/{m}"
        html = fetch(url)
        parsed = parse_calendar_html(html)
        for day, anchors in parsed.items():
            out[(m, day)] = anchors
    return out


def classify_anchors(anchors: set[str]) -> list[dict]:
    """アンカー集合を COLLECTION_GROUPS に当てはめて表示行のリストに変換。"""
    result: list[dict] = []
    for group_anchors, main, sub in COLLECTION_GROUPS:
        if group_anchors & anchors:
            result.append({"main": main, "sub": sub})
    return result


def derive_schedule(
    calendar: dict[tuple[int, int], set[str]],
    year: int,
) -> tuple[dict[str, dict[str, list[dict]]], set[str]]:
    """曜日 × nth(1..5) の完全テーブルを生成。

    同じ (曜日, nth) に複数のサンプルがある場合は共通部分を採用する。
    第 5 週がない月のデータだけだと nth=5 は空として扱われる点に注意。
    """
    # (weekday, nth) → list of anchor sets
    buckets: dict[tuple[int, int], list[set[str]]] = {}
    unknown_anchors: set[str] = set()

    for (month, day), anchors in calendar.items():
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        wd = d.weekday()
        nth = (day - 1) // 7 + 1
        buckets.setdefault((wd, nth), []).append(anchors)
        unknown_anchors |= anchors - KNOWN_ANCHORS

    schedule: dict[str, dict[str, list[dict]]] = {}
    for (wd, nth), samples in buckets.items():
        common = set.intersection(*samples) if samples else set()
        rows = classify_anchors(common)
        # rows が空でも「収集なし」として記録する
        schedule.setdefault(str(wd), {})[str(nth)] = rows

    return schedule, unknown_anchors


def build_district_entry(
    district_id: int,
    name: str,
    year: int,
    months: list[int],
) -> tuple[dict, set[str]]:
    cal = fetch_district_calendar(district_id, year, months)
    schedule, unknown = derive_schedule(cal, year)
    return {"name": name, "schedule": schedule}, unknown


def build_all_districts(year: int, months: list[int]) -> dict:
    districts = fetch_district_list()
    print(f"[info] fetched district list: {len(districts)} districts", file=sys.stderr)

    result: dict[str, dict] = {}
    errors: list[str] = []
    all_unknown: set[str] = set()

    def task(d):
        did, name = d
        try:
            entry, unknown = build_district_entry(did, name, year, months)
            return did, name, entry, unknown, None
        except Exception as e:
            return did, name, None, set(), str(e)

    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(task, d) for d in districts]
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            did, name, entry, unknown, err = fut.result()
            if err:
                errors.append(f"{did} ({name}): {err}")
                print(f"[error] {did} {name}: {err}", file=sys.stderr)
            else:
                result[str(did)] = entry
                all_unknown |= unknown
            if i % 20 == 0:
                print(f"[progress] {i}/{len(districts)} done", file=sys.stderr)
            time.sleep(BATCH_SLEEP / MAX_WORKERS)
    elapsed = time.time() - start
    print(f"[info] done in {elapsed:.1f}s (errors={len(errors)})", file=sys.stderr)
    if all_unknown:
        print(f"[warn] unknown anchors encountered: {sorted(all_unknown)}", file=sys.stderr)

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": BASE_URL,
        "year": year,
        "months": months,
        "districts": dict(sorted(result.items(), key=lambda kv: int(kv[0]))),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument(
        "--months",
        type=int,
        nargs="+",
        default=[1, 4, 10],
        help="取得月（デフォルト 1,4,10: 第5週を含む月で網羅）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent.parent / "lambda" / "districts.json",
    )
    args = parser.parse_args()

    data = build_all_districts(args.year, args.months)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[info] wrote {args.output}", file=sys.stderr)
    print(f"[info] districts: {len(data['districts'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
