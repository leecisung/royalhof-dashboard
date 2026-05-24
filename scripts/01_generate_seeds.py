# -*- coding: utf-8 -*-
"""
01_generate_seeds.py — 1회성 실행
100~150만 키워드 조합 생성 → reserve_pool.db 삽입

실행: python scripts/01_generate_seeds.py
     python scripts/01_generate_seeds.py --dry-run   (삽입 없이 통계만)
     python scripts/01_generate_seeds.py --group G1  (특정 그룹만)
"""

import sys
import json
import logging
import argparse
from itertools import product
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.reserve_pool import init_db, bulk_insert, get_stats

# ──────────────────────────────────────────────
# 로깅
# ──────────────────────────────────────────────
LOG_FILE = ROOT / "logs" / "api_calls.log"
LOG_FILE.parent.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("01_generate_seeds")

# ──────────────────────────────────────────────
# 데이터 로딩
# ──────────────────────────────────────────────

def load_json(filename: str) -> dict | list:
    path = ROOT / "data" / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────
# 그룹 → adgroup_key 매핑
# 서울: 구별 분리 / 경기: 클러스터 분리
# ──────────────────────────────────────────────

# 경기 시군 → 클러스터 매핑
GYEONGGI_CLUSTER: dict[str, str] = {}
for city in ["수원시","팔달구","영통구","권선구","장안구","성남시","분당구","수정구","중원구",
             "과천시","의왕시","군포시","안양시","만안구","동안구","용인시","수지구","기흥구",
             "처인구","오산시","평택시","안성시"]:
    GYEONGGI_CLUSTER[city] = "G2_지역창업_경기_남부"
for city in ["부천시","광명시","시흥시","안산시","상록구","단원구","김포시","화성시"]:
    GYEONGGI_CLUSTER[city] = "G2_지역창업_경기_서부"
for city in ["고양시","일산동구","일산서구","덕양구","파주시","의정부시","양주시",
             "동두천시","포천시","연천군","가평군"]:
    GYEONGGI_CLUSTER[city] = "G2_지역창업_경기_북부"
for city in ["남양주시","구리시","하남시","광주시","이천시","여주시","양평군"]:
    GYEONGGI_CLUSTER[city] = "G2_지역창업_경기_동부"


def get_region_group_key(region: dict) -> str:
    """지역 항목 하나를 adgroup_key로 변환."""
    sido  = region.get("sido", "")
    rtype = region.get("type", "")
    name  = region.get("name", "")
    gu    = region.get("gu", "")      # 동 항목의 소속 구
    gu_of = region.get("gu_of", "")  # 경기 구 항목의 소속 시

    if sido == "서울":
        if rtype == "시도":
            return "G2_지역창업_서울"
        elif rtype == "구":
            return f"G2_지역창업_서울_{name}"
        elif rtype == "동":
            return f"G2_지역창업_서울_{gu}" if gu else "G2_지역창업_서울"
    elif sido == "경기":
        if rtype == "시도":
            return "G2_지역창업_경기"
        lookup = gu_of if rtype == "구" else name
        return GYEONGGI_CLUSTER.get(lookup, GYEONGGI_CLUSTER.get(name, "G2_지역창업_경기"))
    return SIDO_GROUP_MAP.get(sido, f"G2_지역창업_{sido}")


SIDO_GROUP_MAP = {
    "서울":  "G2_지역창업_서울",
    "경기":  "G2_지역창업_경기",
    "부산":  "G2_지역창업_부산",
    "대구":  "G2_지역창업_대구",
    "인천":  "G2_지역창업_인천",
    "광주":  "G2_지역창업_광주",
    "대전":  "G2_지역창업_대전",
    "울산":  "G2_지역창업_울산",
    "세종":  "G2_지역창업_세종",
    "강원":  "G2_지역창업_강원",
    "충북":  "G2_지역창업_충북",
    "충남":  "G2_지역창업_충남",
    "전북":  "G2_지역창업_전북",
    "전남":  "G2_지역창업_전남",
    "경북":  "G2_지역창업_경북",
    "경남":  "G2_지역창업_경남",
    "제주":  "G2_지역창업_제주",
}


# ──────────────────────────────────────────────
# G1 메인변형 생성
# ──────────────────────────────────────────────

def gen_g1(seeds: dict, suffixes: dict) -> list[dict]:
    """치킨창업/호프창업 × 창업형+가맹형+본사형+비용형 접미사"""
    bases = seeds["G1_메인변형"]["bases"]
    extra = seeds["G1_메인변형"]["extra_suffixes"]
    all_suffixes = set(extra)
    for key in ["창업형", "가맹형", "본사형", "비용형"]:
        all_suffixes.update(suffixes.get(key, []))

    results = []
    for base in bases:
        for suf in all_suffixes:
            kw = base if suf in base else f"{base}{suf}"
            if kw != base:  # 이미 접미사 포함된 base는 중복 방지
                results.append({"keyword": kw, "adgroup_key": "G1_메인변형"})
        # base 자체도 등록
        results.append({"keyword": base, "adgroup_key": "G1_메인변형"})

    return results


# ──────────────────────────────────────────────
# G2 지역창업 생성
# 패턴: {지역명}{카테고리베이스} + 접미사
# 예: 강남치킨창업, 강남구치킨창업비용, 역삼동호프창업문의
# ──────────────────────────────────────────────

def gen_g2(seeds: dict, regions: list, suffixes: dict) -> list[dict]:
    category_bases = seeds["G2_지역창업"]["category_bases"]
    region_suffixes = suffixes.get("지역결합형", ["창업", "가맹점", "창업비용"])

    # 지역명 정규화 - '동', '구', '시', '군' 붙은 것과 떼어낸 것 모두 생성
    def region_variants(r: dict) -> list[str]:
        name = r["name"]
        variants = [name]
        # 행정 단위 접미사 제거한 축약형
        for suffix in ["동", "구", "시", "군"]:
            if name.endswith(suffix) and len(name) > 1:
                short = name[:-1]
                if short not in variants:
                    variants.append(short)
        return variants

    results = []
    for region in regions:
        group_key = get_region_group_key(region)
        for region_name in region_variants(region):
            for cat_base in category_bases:
                # 기본: 지역+카테고리
                kw_base = f"{region_name}{cat_base}"
                results.append({"keyword": kw_base, "adgroup_key": group_key})
                # 추가 접미사
                for suf in region_suffixes:
                    # cat_base에 이미 접미사가 있으면 중복 방지
                    if not cat_base.endswith(suf):
                        kw = f"{region_name}{cat_base}{suf}"
                        results.append({"keyword": kw, "adgroup_key": group_key})

    return results


# ──────────────────────────────────────────────
# G3 경쟁치킨 생성
# ──────────────────────────────────────────────

def gen_g3(seeds: dict, competitors: dict, suffixes: dict) -> list[dict]:
    brands = competitors["치킨"]
    extra = seeds["G3_경쟁치킨"]["extra_suffixes"]
    all_suffixes = set(extra)
    all_suffixes.update(suffixes.get("창업형", []))
    all_suffixes.update(suffixes.get("가맹형", []))

    results = []
    for brand in brands:
        for suf in all_suffixes:
            kw = f"{brand}{suf}"
            results.append({"keyword": kw, "adgroup_key": "G3_경쟁치킨"})
        results.append({"keyword": brand, "adgroup_key": "G3_경쟁치킨"})

    return results


# ──────────────────────────────────────────────
# G4 경쟁호프 생성
# ──────────────────────────────────────────────

def gen_g4(seeds: dict, competitors: dict, suffixes: dict) -> list[dict]:
    brands = competitors["호프맥주"]
    extra = seeds["G4_경쟁호프"]["extra_suffixes"]
    all_suffixes = set(extra)
    all_suffixes.update(suffixes.get("창업형", []))
    all_suffixes.update(suffixes.get("가맹형", []))

    results = []
    for brand in brands:
        for suf in all_suffixes:
            kw = f"{brand}{suf}"
            results.append({"keyword": kw, "adgroup_key": "G4_경쟁호프"})
        results.append({"keyword": brand, "adgroup_key": "G4_경쟁호프"})

    return results


# ──────────────────────────────────────────────
# G5 상황조건 생성
# ──────────────────────────────────────────────

def gen_g5(seeds: dict, suffixes: dict) -> list[dict]:
    bases = seeds["G5_상황조건"]["bases"]
    # 상황조건 키워드는 자체가 완성형이라 접미사 최소화
    extra = ["", "문의", "상담", "알아보기", "추천"]

    results = []
    for base in bases:
        results.append({"keyword": base, "adgroup_key": "G5_상황조건"})
        for suf in extra:
            if suf and not base.endswith(suf):
                results.append({"keyword": f"{base}{suf}", "adgroup_key": "G5_상황조건"})

    return results


# ──────────────────────────────────────────────
# G6 메뉴결합 생성
# ──────────────────────────────────────────────

def gen_g6(seeds: dict, suffixes: dict) -> list[dict]:
    bases = seeds["G6_메뉴결합"]["bases"]
    all_suffixes = set(suffixes.get("창업형", []))
    all_suffixes.update(suffixes.get("가맹형", []))
    all_suffixes.update(["비용", "문의", "상담", "조건"])

    results = []
    for base in bases:
        results.append({"keyword": base, "adgroup_key": "G6_메뉴결합"})
        for suf in all_suffixes:
            if not base.endswith(suf):
                results.append({"keyword": f"{base}{suf}", "adgroup_key": "G6_메뉴결합"})

    return results


# ──────────────────────────────────────────────
# GR 지역 × 경쟁사 조합
# 패턴: {지역}{브랜드}{접미사}  예: 강남BBQ창업, 역삼동교촌가맹비
# ──────────────────────────────────────────────

def gen_region_competitor(regions: list, competitors: dict, suffixes: dict) -> list[dict]:
    all_brands = competitors.get("치킨", []) + competitors.get("호프맥주", [])
    region_suffixes = ["창업", "가맹", "창업비용", "가맹비", "창업문의", "창업조건"]

    def region_variants(r: dict) -> list[str]:
        name = r["name"]
        variants = [name]
        for suffix in ["동", "구", "시", "군"]:
            if name.endswith(suffix) and len(name) > 1:
                short = name[:-1]
                if short not in variants:
                    variants.append(short)
        return variants

    results = []
    for region in regions:
        group_key = get_region_group_key(region)
        for region_name in region_variants(region):
            for brand in all_brands:
                for suf in region_suffixes:
                    kw = f"{region_name}{brand}{suf}"
                    results.append({"keyword": kw, "adgroup_key": group_key})
    return results


# ──────────────────────────────────────────────
# 유효성 필터
# ──────────────────────────────────────────────

def filter_keywords(keywords: list[dict]) -> list[dict]:
    """중복 제거 + 기본 품질 필터"""
    seen = set()
    results = []
    for item in keywords:
        kw = item["keyword"].strip()
        # 너무 짧거나 긴 키워드 제거
        if len(kw) < 3 or len(kw) > 30:
            continue
        if kw in seen:
            continue
        seen.add(kw)
        results.append({"keyword": kw, "adgroup_key": item["adgroup_key"]})
    return results


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="DB 삽입 없이 통계만 출력")
    parser.add_argument("--group", default="", help="특정 그룹만 생성 (예: G1, G2, G3)")
    args = parser.parse_args()

    logger.info("=== 01_generate_seeds 시작 ===")

    seeds      = load_json("seeds.json")
    regions    = load_json("regions.json")
    competitors = load_json("competitors.json")
    suffixes   = load_json("suffixes.json")

    all_keywords: list[dict] = []

    run_all = not args.group

    if run_all or args.group.upper().startswith("G1"):
        g1 = gen_g1(seeds, suffixes)
        logger.info("G1_메인변형: %d개 생성", len(g1))
        all_keywords.extend(g1)

    if run_all or args.group.upper().startswith("G2"):
        g2 = gen_g2(seeds, regions, suffixes)
        logger.info("G2_지역창업: %d개 생성", len(g2))
        all_keywords.extend(g2)

    if run_all or args.group.upper().startswith("G3"):
        g3 = gen_g3(seeds, competitors, suffixes)
        logger.info("G3_경쟁치킨: %d개 생성", len(g3))
        all_keywords.extend(g3)

    if run_all or args.group.upper().startswith("G4"):
        g4 = gen_g4(seeds, competitors, suffixes)
        logger.info("G4_경쟁호프: %d개 생성", len(g4))
        all_keywords.extend(g4)

    if run_all or args.group.upper().startswith("G5"):
        g5 = gen_g5(seeds, suffixes)
        logger.info("G5_상황조건: %d개 생성", len(g5))
        all_keywords.extend(g5)

    if run_all or args.group.upper().startswith("G6"):
        g6 = gen_g6(seeds, suffixes)
        logger.info("G6_메뉴결합: %d개 생성", len(g6))
        all_keywords.extend(g6)

    if run_all or args.group.upper().startswith("GR"):
        gr = gen_region_competitor(regions, competitors, suffixes)
        logger.info("GR_지역×경쟁사: %d개 생성", len(gr))
        all_keywords.extend(gr)

    # 필터 및 중복 제거
    filtered = filter_keywords(all_keywords)
    logger.info("필터 후 최종: %d개 (원본 %d개)", len(filtered), len(all_keywords))

    # 그룹별 통계 출력
    from collections import Counter
    group_counts = Counter(item["adgroup_key"] for item in filtered)
    logger.info("=== 그룹별 키워드 수 ===")
    for group, cnt in sorted(group_counts.items()):
        logger.info("  %-30s: %d개", group, cnt)
    logger.info("  %-30s: %d개", "합계", sum(group_counts.values()))

    if args.dry_run:
        logger.info("DRY RUN — DB 삽입 생략")
        return

    # DB 삽입
    init_db()
    inserted = bulk_insert(filtered)
    logger.info("DB 삽입 완료: %d개 신규", inserted)

    # 최종 상태
    stats = get_stats()
    logger.info("예비 풀 현황: %s", stats)
    logger.info("=== 01_generate_seeds 완료 ===")


if __name__ == "__main__":
    main()
