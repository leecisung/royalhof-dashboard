# -*- coding: utf-8 -*-
"""
nas_scheduler.py — NAS(도커 python:3.11) 상주 스케줄러

컨테이너 안에서 무한 루프로 KST 기준:
  - 매시 :02  → autoplace.py (플레이스 자동입찰)
  - 월 07:00  → update_lotte_games.py (홈경기 일정 갱신)
place_watch(순위감시+git push)는 PC 담당 (깃 인증이 PC에 있음).

  docker run -d --restart unless-stopped --name autoplace \
    -v /volume1/docker/royalhof:/app -w /app python:3.11-slim \
    bash -c "pip install -q requests python-dotenv && python scripts/nas_scheduler.py"
"""
import sys
import time
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parents[1]
KST = timezone(timedelta(hours=9))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(ROOT / "logs" / "nas_scheduler.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("nas_scheduler")


def run(script: str):
    logger.info(">>> %s 실행", script)
    parts = script.split()
    try:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / parts[0]), *parts[1:]],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1800)
        tail = (r.stdout or r.stderr or "").strip().splitlines()[-3:]
        logger.info("<<< %s 종료 rc=%d %s", script, r.returncode, " | ".join(tail))
    except Exception as e:
        logger.error("%s 실패: %s", script, e)


def main():
    (ROOT / "logs").mkdir(exist_ok=True)
    logger.info("=== NAS 스케줄러 시작 (KST %s) ===", datetime.now(KST).strftime("%m/%d %H:%M"))
    done = {}
    while True:
        now = datetime.now(KST)
        hkey = now.strftime("%Y-%m-%d %H")
        dkey = now.strftime("%Y-%m-%d")
        if now.minute >= 2 and done.get("autoplace") != hkey:
            done["autoplace"] = hkey
            run("autoplace.py")
        if now.weekday() == 0 and now.hour >= 7 and done.get("lotte") != dkey:
            done["lotte"] = dkey
            run("update_lotte_games.py")
        if (now.hour, now.minute) >= (8, 30) and done.get("watch") != dkey:
            done["watch"] = dkey
            run("place_watch.py --no-push")
        time.sleep(30)


if __name__ == "__main__":
    main()
