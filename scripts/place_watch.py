# -*- coding: utf-8 -*-
"""
place_watch.py — 사직점 플레이스 일일 감시기 (매일 08:30 schtasks: place_watch_daily)

1. 격전지 키워드 오가닉 순위 조회 → 어제 대비 변동(진입/이탈/±2계단) 카톡
2. 스마트플레이스 대표키워드(keywordList) 변경 감지 → 카톡
3. 이력 CSV 누적 + git 커밋·푸시 → Vercel 대시보드(/place 순위 추이) 자동 갱신
4. 월요일: sajik_tag_plan.py 전체 재조사(검색량+순위) → 신규 기회 키워드 카톡

  python scripts/place_watch.py            # 일일 감시
  python scripts/place_watch.py --replan   # 주간 재조사 강제 실행
  python scripts/place_watch.py --no-push  # git push 생략
"""
import sys
import re
import csv
import json
import time
import argparse
import logging
import subprocess
import urllib.parse
from datetime import date, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(ROOT / "logs" / "place_watch.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("place_watch")

CFG = ROOT / "data" / "place_rank_keywords.json"
STATE = ROOT / "data" / "place_watch_state.json"
HISTORY = ROOT / "data" / "place_rank_history.csv"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 Mobile Safari/604.1")


def fetch_rank(query: str, x: str, y: str, pid: str):
    """(오가닉순위, 목록크기, 광고수) — 광고수는 괄호(광고포함 순위) 표시용."""
    url = (f"https://m.place.naver.com/restaurant/list"
           f"?query={urllib.parse.quote(query)}&x={x}&y={y}")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        r.encoding = "utf-8"
        m = re.search(r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\});", r.text, re.S)
        if not m:
            return None, -1, None
        d = json.loads(m.group(1))
        ids = [str(v.get("id")) for k, v in d.items()
               if k.startswith("PlaceListBusinessesItem:")]
        ads_n, seen = 0, set()
        for k, v in d.get("ROOT_QUERY", {}).items():
            if k.startswith("adBusinesses"):
                items = v.get("items") if isinstance(v, dict) else v
                for it in (items if isinstance(items, list) else []):
                    if isinstance(it, dict) and it.get("__ref") and it["__ref"] not in seen:
                        seen.add(it["__ref"])
                        ads_n += 1
        return (ids.index(pid) + 1 if pid in ids else None), len(ids), ads_n
    except Exception as e:
        logger.warning("'%s' 조회 실패: %s", query, e)
        return None, -1, None


def fetch_current_keywords(pid: str) -> list:
    try:
        r = requests.get(f"https://m.place.naver.com/restaurant/{pid}/home",
                         headers={"User-Agent": UA}, timeout=15)
        r.encoding = "utf-8"
        m = re.search(r'"keywordList":\s*(\[[^\]]*\])', r.text)
        return json.loads(m.group(1)) if m else []
    except Exception as e:
        logger.warning("대표키워드 조회 실패: %s", e)
        return []


def git_push(paths: list):
    def run(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    run("add", *paths)
    if run("diff", "--cached", "--quiet").returncode == 0:
        logger.info("git: 변경 없음")
        return
    run("commit", "-m", f"auto(place-watch): {date.today().isoformat()} 순위·이력 갱신")
    run("pull", "--rebase", "origin", "main")
    r = run("push", "origin", "main")
    logger.info("git push: %s", "OK" if r.returncode == 0 else r.stderr[:200])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replan", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    pid = str(cfg.get("place_id"))
    locs = cfg.get("locations") or [{"name": "기본", "x": cfg.get("x"), "y": cfg.get("y")}]
    loc = locs[0]
    today = date.today().isoformat()

    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    prev_ranks = state.get("ranks", {})
    alerts = []

    # 감시 대상: config 키워드(변동 알림 대상) + 태그플랜 검색량 100+ 전체(요일별 기록용)
    alert_kws = set(cfg.get("keywords", []))
    try:
        plan = json.loads((ROOT / "data" / "sajik_tag_plan.json").read_text(encoding="utf-8"))
        extra = [r["kw"] for r in plan.get("rows", []) if r.get("total", 0) >= 100]
    except Exception:
        extra = []
    all_kws = list(dict.fromkeys(list(alert_kws) + extra))[:45]

    # 1. 순위 조회 + 변동 감지 (알림은 config 키워드만, 기록은 전체)
    ranks, rows = {}, []
    for kw in all_kws:
        rank, size, ads_n = fetch_rank(kw, loc.get("x"), loc.get("y"), pid)
        ranks[kw] = rank
        rows.append([today, kw, loc.get("name"), rank if rank else "", size, "",
                     ads_n if ads_n is not None else ""])
        old = prev_ranks.get(kw)
        shown = f"{rank}위" if rank else "밖"
        logger.info("  %s → %s (이전 %s)", kw, shown, f"{old}위" if old else "밖/신규")
        if kw in prev_ranks and kw in alert_kws:
            if old and not rank:
                alerts.append(f"🔻 '{kw}' {old}위→리스트 이탈")
            elif not old and rank:
                alerts.append(f"🎉 '{kw}' 신규 진입 {rank}위")
            elif old and rank and abs(rank - old) >= 2:
                arrow = "🔺" if rank < old else "🔻"
                alerts.append(f"{arrow} '{kw}' {old}위→{rank}위")
        time.sleep(1.0)

    # 2. 대표키워드 변경 감지
    cur_kws = fetch_current_keywords(pid)
    if cur_kws:
        old_kws = state.get("rep_keywords")
        if old_kws is not None and set(cur_kws) != set(old_kws):
            alerts.append(f"🏷 대표키워드 변경: {old_kws} → {cur_kws}")
        state["rep_keywords"] = cur_kws

    # 3. 이력 저장
    new_file = not HISTORY.exists()
    with open(HISTORY, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "keyword", "location", "rank", "list_size", "top1", "ads"])
        w.writerows(rows)

    state["ranks"] = ranks
    state["last_run"] = today
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    # 4. 월요일(또는 --replan): 태그 플랜 전체 재조사
    push_paths = ["data/place_rank_history.csv", "data/place_watch_state.json"]
    if args.replan or datetime.now().weekday() == 0:
        logger.info("주간 태그 플랜 재조사 시작")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sajik_tag_plan.py")],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
        if r.returncode == 0:
            push_paths.append("data/sajik_tag_plan.json")
            alerts.append("📊 주간 키워드 재조사 완료 — 대시보드 /place 갱신됨")
        else:
            logger.error("재조사 실패: %s", (r.stderr or "")[:300])

    # 5. 카톡 (변화 있을 때만)
    if alerts:
        try:
            from kakao_send import send_kakao
            send_kakao("[플레이스 감시] " + " / ".join(alerts))
        except Exception as e:
            logger.info("kakao skip: %s", e)
    logger.info("변동 %d건: %s", len(alerts), alerts or "없음")

    # 6. 대시보드 자동 반영
    if not args.no_push:
        git_push(push_paths)


if __name__ == "__main__":
    main()
