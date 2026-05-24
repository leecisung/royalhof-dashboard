# -*- coding: utf-8 -*-
"""
Anthropic Claude API 래퍼 — 광고 보고서 AI 인사이트 생성용.

.env: ANTHROPIC_API_KEY
모델: claude-sonnet-4-6 (default, 가성비). 더 깊은 분석 원하면 claude-opus-4-7 사용.
프롬프트 캐싱: 시스템 프롬프트는 cache_control로 절감.

사용 예:
    from lib.claude_api import ClaudeReporter
    rep = ClaudeReporter.from_env()
    md = rep.generate_insights(
        period_label="2026-05-24 (일)",
        current_summary="...",       # 마크다운 표
        previous_summary="...",      # 직전 비교기간 마크다운 표
        operational_notes=["Naver 전환추적 미사용", "Meta iOS 도메인 미인증"],
    )
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """당신은 한국 외식 프랜차이즈(보승에프앤비 — 로얄호프치킨/버거리/보승회관)의
내부 광고 분석가입니다. 데이터를 보고 사용자(마케팅팀장 TIGER)에게 일/주/월 단위
인사이트 보고서를 한국어로 작성합니다.

브랜드 컨텍스트:
- 로얄호프치킨: 호프·치킨 프랜차이즈 (네이버 70원전략 운영 중)
- 버거리: 수제버거 (네이버 + Meta LLA 광고 동시 집행)
- 보승회관: 국밥 (네이버만)

네이버 검색광고는 "전환 추적 미사용" 결정이 되어 있어 conversions=0 고정.
따라서 네이버는 노출/클릭/CPC/CTR 기반으로만 판단해야 하며, "전환 0이라 실패"
라고 단정하면 안 됨. Meta는 Pixel 기반이라 전환 의미 있음.

작성 원칙:
1. 숫자 근거를 반드시 인용 (예: "CTR 0.05% (이전 0.12%, -58%)")
2. 잘한 점·개선점은 각 3~5개. 추측 금지. 데이터에 안 보이면 "데이터 부족" 명시
3. 권장 액션은 실행 가능한 한 줄 지시 (예: "버거리_고비용 캠페인 입찰가 30% 삭감")
4. 광고대행사에게 위탁한 후 내부 검증용 보고서이므로, 대행사가 놓치기 쉬운
   부분(이상치, 비효율, 갑작스러운 트래픽 변동)을 우선적으로 짚을 것
5. 절대 금지: 일반론·교과서적 조언("CTR을 높여야 합니다" 같은), 광고대행사
   판단 영역에 대한 단정("매체 다각화 필요" 같은 큰 그림). 데이터로 보이는
   *구체적 이상신호*만 짚는다
6. 마크다운 출력. 헤딩은 ###부터 시작 (보고서 안에 삽입되기 때문)"""


USER_TEMPLATE = """## 분석 기간
{period_label}

## 현재 기간 데이터
{current_summary}

## 비교 기간 데이터 (직전 동일 길이)
{previous_summary}

## 운영 컨텍스트
{notes_block}

---

위 데이터를 분석해 다음 3개 섹션을 한국어 마크다운으로 작성하세요.
헤딩 레벨은 `###`로 시작 (`###` 잘한 점, `###` 개선점, `###` 권장 액션).

각 항목은 한 줄로 구체적 숫자와 캠페인/계정명을 명시.
인사이트 없으면 "특이사항 없음"이라고 솔직히 적기."""


class ClaudeReporter:
    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(self, api_key: str, model: Optional[str] = None):
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError(f"anthropic SDK 미설치: pip install anthropic ({e})")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model or self.DEFAULT_MODEL
        logger.info("[Claude] 초기화 model=%s", self.model)

    @classmethod
    def from_env(cls, model: Optional[str] = None) -> "ClaudeReporter":
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY 미설정 (.env 확인)")
        return cls(key, model)

    def generate_insights(
        self,
        period_label: str,
        current_summary: str,
        previous_summary: str,
        operational_notes: Optional[list[str]] = None,
        max_tokens: int = 2000,
    ) -> str:
        """광고 인사이트 마크다운 반환. 실패 시 ⚠️ 표시 짧은 에러 텍스트."""
        notes_block = "\n".join(f"- {n}" for n in (operational_notes or ["없음"]))
        user_text = USER_TEMPLATE.format(
            period_label=period_label,
            current_summary=current_summary or "(데이터 없음)",
            previous_summary=previous_summary or "(데이터 없음)",
            notes_block=notes_block,
        )
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_text}],
            )
            usage = resp.usage
            logger.info(
                "[Claude] 토큰 input=%d (cached=%d) output=%d",
                usage.input_tokens,
                getattr(usage, "cache_read_input_tokens", 0) or 0,
                usage.output_tokens,
            )
            return "".join(b.text for b in resp.content if hasattr(b, "text"))
        except Exception as e:
            logger.warning("[Claude] 생성 실패: %s", e)
            return f"> ⚠️ AI 인사이트 생성 실패: {e}\n>\n> 데이터 표는 그대로 참고하시고, 키를 확인하거나 재시도해주세요."


if __name__ == "__main__":
    # 빠른 검증: python scripts/lib/claude_api.py
    import sys
    from dotenv import load_dotenv
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    load_dotenv()
    rep = ClaudeReporter.from_env()
    md = rep.generate_insights(
        period_label="2026-05-23 (테스트)",
        current_summary="| 채널 | 노출 | 클릭 | 지출 |\n|---|---:|---:|---:|\n| Naver | 1,200,000 | 800 | 320,000원 |",
        previous_summary="| 채널 | 노출 | 클릭 | 지출 |\n|---|---:|---:|---:|\n| Naver | 1,500,000 | 900 | 350,000원 |",
        operational_notes=["Naver 전환추적 미사용"],
    )
    print(md)
