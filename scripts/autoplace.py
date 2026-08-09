# -*- coding: utf-8 -*-
"""
autoplace.py — 오토플레이스 클론: 플레이스 광고그룹 요일×시간대 자동 입찰 (dayparting)

원조(오토플레이스)는 광고그룹 40개로 쪼개 시간대별 입찰을 관리하지만,
플레이스 그룹은 API 생성 불가(code 1018)이므로 우리는 그룹 1개의 bidAmt를
매시간 cron으로 바꿔 같은 효과를 낸다. 사직 특화로 롯데 홈경기일 boost 지원.

설정: data/autoplace_config.json (스케줄), data/lotte_home_games.json (홈경기일)

  python scripts/autoplace.py                 # 현재 시각 기준 적용
  python scripts/autoplace.py --dry-run       # 시뮬레이션 (변경 안 함)
  python scripts/autoplace.py --status        # 주간 스케줄표 + 최근 7일 성과
  python scripts/autoplace.py --at "2026-08-14 18:00"   # 시각 가정 테스트

매시간 정각 cron: scripts/run_autoplace.bat (작업 스케줄러 autoplace_hourly)
"""
import os
import sys
import json
import argparse
import logging
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv
from lib.naver_api import NaverAdAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(ROOT / "logs" / "autoplace.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("autoplace")

KST = timezone(timedelta(hours=9))
CFG = ROOT / "data" / "autoplace_config.json"
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_windows(sched: dict) -> list:
    """{"17-22": 450} → [(17, 22, 450), ...] 정렬."""
    out = []
    for rng, val in sched.items():
        if rng.startswith("_"):
            continue
        try:
            s, e = rng.split("-")
            out.append((int(s), int(e), val))
        except (ValueError, AttributeError):
            logger.warning("스케줄 구간 무시: %r", rng)
    return sorted(out)


def bid_for(target: dict, when: datetime, event_dates: set) -> tuple:
    """해당 시각의 목표 입찰가 계산. (bid, 설명) 반환."""
    day_key = DAY_KEYS[when.weekday()]
    sched = target.get("schedule", {})
    day_sched = sched.get(day_key) or sched.get("default") or {}
    base = None
    for s, e, val in parse_windows(day_sched):
        if s <= when.hour < e:
            base = int(val)
            break
    if base is None:
        base = int(target.get("bid_min", 70))
    desc = f"{day_key} {when.hour:02d}시 기본{base}"

    ev = target.get("event", {})
    if ev and when.strftime("%Y-%m-%d") in event_dates:
        for s, e, mult in parse_windows(ev.get("boost_windows", {})):
            if s <= when.hour < e:
                base = int(round(base * float(mult)))
                desc += f" ×홈경기{mult}={base}"
                break

    lo, hi = int(target.get("bid_min", 70)), int(target.get("bid_max", 100000))
    bid = max(lo, min(hi, base))
    if bid != base:
        desc += f" →캡{bid}"
    return bid, desc


def load_event_dates(target: dict) -> set:
    f = target.get("event", {}).get("dates_file")
    if not f:
        return set()
    p = ROOT / f
    if not p.exists():
        logger.warning("이벤트 파일 없음: %s", f)
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data.get("dates", []))
    except Exception as e:
        logger.error("이벤트 파일 파싱 실패 %s: %s", f, e)
        return set()


def make_api(target: dict) -> NaverAdAPI:
    pfx = target.get("env_prefix", "NAVER_AD")
    return NaverAdAPI(os.getenv(f"{pfx}_API_KEY"),
                      os.getenv(f"{pfx}_SECRET_KEY"),
                      os.getenv(f"{pfx}_CUSTOMER_ID"))


def apply_target(target: dict, when: datetime, dry: bool) -> None:
    name = target.get("name", "?")
    gid = target.get("adgroup_id")
    event_dates = load_event_dates(target)
    want, desc = bid_for(target, when, event_dates)

    api = make_api(target)
    g = api._request("GET", f"/ncc/adgroups/{gid}")
    cur = int(g.get("bidAmt") or 0)
    changed = []

    # 휴무일 (off_days: ["mon"]) → 하루 종일 OFF + 입찰 조정 스킵
    off_day = DAY_KEYS[when.weekday()] in [str(d).lower() for d in target.get("off_days", [])]

    # 영업시간 ON/OFF (on_hours: "11-22" → 그 시간대만 광고 ON, 밖이면 userLock)
    on_hours = target.get("on_hours")
    if on_hours:
        try:
            s, e = (int(x) for x in on_hours.split("-"))
            want_on = (s <= when.hour < e) and not off_day
            if off_day:
                desc = "휴무일"
            is_on = not bool(g.get("userLock"))
            if want_on != is_on:
                changed.append("ON" if want_on else "OFF(마감)")
                logger.info("[%s] 광고 %s → %s (영업 %s시)%s", name,
                            "ON" if is_on else "OFF", "ON" if want_on else "OFF",
                            on_hours, " [DRY-RUN]" if dry else "")
                if not dry:
                    body = dict(g)
                    body["userLock"] = not want_on
                    g = api._request("PUT", f"/ncc/adgroups/{gid}",
                                     params={"fields": "userLock"}, body=body)
        except ValueError:
            logger.warning("[%s] on_hours 형식 오류: %r", name, on_hours)
    elif g.get("status") == "PAUSED":
        logger.warning("[%s] 그룹 PAUSED — 입찰가만 갱신, 광고는 안 나감", name)

    if cur != want and not off_day:
        changed.append(f"{cur}→{want}원")
        logger.info("[%s] %d → %d원 (%s)%s", name, cur, want, desc,
                    " [DRY-RUN]" if dry else "")
        if not dry:
            body = dict(g)
            body["bidAmt"] = want
            api._request("PUT", f"/ncc/adgroups/{gid}",
                         params={"fields": "bidAmt"}, body=body)
    if not changed:
        logger.info("[%s] 유지 %d원 (%s)", name, cur, desc)
    elif not dry:
        try:
            from kakao_send import send_kakao
            send_kakao(f"[오토플레이스] {name} {' / '.join(changed)} ({desc})")
        except Exception as e:
            logger.info("kakao skip: %s", e)


def show_status(target: dict, when: datetime) -> None:
    name = target.get("name", "?")
    event_dates = load_event_dates(target)
    print(f"\n■ {name}  (adgroup {target.get('adgroup_id')})")

    print("\n주간 스케줄 (원):")
    hours = range(24)
    print("      " + " ".join(f"{h:>4d}" for h in hours))
    for i, dk in enumerate(DAY_KEYS):
        probe_day = when + timedelta(days=(i - when.weekday()))
        row = []
        for h in hours:
            t = probe_day.replace(hour=h)
            b, _ = bid_for(target, t, set())
            row.append(f"{b:>4d}")
        print(f"{dk:>4s}  " + " ".join(row))

    upcoming = sorted(d for d in event_dates if d >= when.strftime("%Y-%m-%d"))
    if upcoming:
        ev = target.get("event", {})
        print(f"\n홈경기 boost {ev.get('boost_windows')} 적용 예정일: {', '.join(upcoming)}")

    try:
        api = make_api(target)
        gid = target.get("adgroup_id")
        g = api._request("GET", f"/ncc/adgroups/{gid}")
        print(f"\n현재 입찰가: {g.get('bidAmt')}원  상태: {g.get('status')}")
        until = date.today() - timedelta(days=1)
        since = until - timedelta(days=6)
        res = api._request("GET", "/stats", params={
            "ids": gid,
            "fields": '["impCnt","clkCnt","salesAmt"]',
            "timeRange": json.dumps({"since": str(since), "until": str(until)}),
        })
        rows = res.get("data", []) if isinstance(res, dict) else []
        for r in rows:
            imp = int(r.get("impCnt", 0) or 0)
            clk = int(r.get("clkCnt", 0) or 0)
            cost = int(r.get("salesAmt", 0) or 0)
            cpc = cost // clk if clk else 0
            print(f"최근 7일: 노출 {imp:,} / 클릭 {clk} / 비용 {cost:,}원 / CPC {cpc:,}원")
    except Exception as e:
        print(f"(성과 조회 실패: {e})")


def main():
    ap = argparse.ArgumentParser(description="오토플레이스 클론 — 요일×시간대 자동 입찰")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--at", help='시각 가정 (예: "2026-08-14 18:00")')
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    when = (datetime.strptime(args.at, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
            if args.at else datetime.now(KST))

    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    targets = [t for t in cfg.get("targets", []) if t.get("enabled")]
    if not targets:
        logger.warning("enabled 타겟 없음")
        return

    if args.status:
        for t in targets:
            show_status(t, when)
        return

    logger.info("=== autoplace %s (%s) 타겟 %d개 ===",
                when.strftime("%m/%d %H:%M"), "DRY-RUN" if args.dry_run else "LIVE",
                len(targets))
    for t in targets:
        try:
            apply_target(t, when, args.dry_run)
        except Exception as e:
            logger.error("[%s] 실패: %s", t.get("name"), e)


if __name__ == "__main__":
    main()
