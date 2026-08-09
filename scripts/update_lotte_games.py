# -*- coding: utf-8 -*-
"""
update_lotte_games.py — KBO 공식 일정 API에서 롯데 사직 홈경기 자동 갱신

koreabaseball.com GetScheduleList로 이번 달~11월 사직 홈경기(취소 제외)를 받아
data/lotte_home_games.json 갱신. 재편성(우천/폭염 취소분) 발표도 자동 반영.
매주 월요일 schtasks(lotte_games_weekly) + 수동 실행 가능.

  python scripts/update_lotte_games.py
"""
import sys
import re
import json
import logging
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).parents[1]
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(ROOT / "logs" / "update_lotte_games.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("lotte_games")

API = "https://www.koreabaseball.com/ws/Schedule.asmx/GetScheduleList"
HEADERS = {"User-Agent": "Mozilla/5.0",
           "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
           "Referer": "https://www.koreabaseball.com/Schedule/Schedule.aspx"}
OUT = ROOT / "data" / "lotte_home_games.json"


def fetch_month(year: int, month: int) -> list:
    """해당 월 사직 홈경기 날짜 리스트 (취소 경기 제외)."""
    data = {"leId": "1", "srIdList": "0,9,6", "seasonId": str(year),
            "gameMonth": f"{month:02d}", "teamId": "LT"}
    r = requests.post(API, headers=HEADERS, data=data, timeout=15)
    r.raise_for_status()
    out = []
    for row in r.json().get("rows", []):
        cells = [re.sub(r"<[^>]+>", "", c.get("Text", "") or "") for c in row.get("row", [])]
        if len(cells) < 8:
            continue
        day_txt, stadium, remark = cells[0], cells[-2], cells[-1]
        if stadium != "사직" or "취소" in remark:
            continue
        m = re.match(r"(\d{2})\.(\d{2})", day_txt)
        if m:
            out.append(f"{year}-{m.group(1)}-{m.group(2)}")
    return out


def main():
    today = date.today()
    dates = []
    for month in range(today.month, 12):
        try:
            got = fetch_month(today.year, month)
            dates.extend(got)
            logger.info("%d월: 사직 홈경기 %d건", month, len(got))
        except Exception as e:
            logger.error("%d월 조회 실패: %s", month, e)

    if not dates:
        logger.warning("조회 결과 0건 — 기존 파일 유지 (API 형식 변경 가능성)")
        return

    try:
        old = set(json.loads(OUT.read_text(encoding="utf-8")).get("dates", []))
    except Exception:
        old = set()
    new = sorted(set(dates))
    added = sorted(set(new) - old)
    removed = sorted(d for d in old - set(new) if d >= str(today))

    OUT.write_text(json.dumps({
        "_comment": "롯데 사직 홈경기 (KBO 공식 API 자동갱신 — scripts/update_lotte_games.py, 매주 월요일)",
        "_updated": str(today),
        "dates": new,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("갱신 완료: %d건 (추가 %s / 제거 %s)", len(new),
                added or "없음", removed or "없음")

    if added or removed:
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            from kakao_send import send_kakao
            msg = "[오토플레이스] 홈경기 일정 갱신"
            if added:
                msg += f" +{','.join(d[5:] for d in added)}"
            if removed:
                msg += f" -{','.join(d[5:] for d in removed)}"
            send_kakao(msg)
        except Exception as e:
            logger.info("kakao skip: %s", e)


if __name__ == "__main__":
    main()
