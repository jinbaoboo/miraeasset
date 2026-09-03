"""Build the deterministic 100-question development evaluation set.

Every case inherits a source-audited expectation from an existing gold set, but
uses a newly authored question.  Keeping the expectations in one place avoids
silently changing the answer key while iterating on retrieval and rendering.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "eval" / "development_qa_100_questions.jsonl"


# Two independent phrasings per source case.  25 sources x 2 = 50 close cases.
CLOSE_VARIANTS = {
    "strong-close-001": [
        "2025년 1분기 카카오 연결 손익계산서의 영업수익 수치와 단위를 알려줘.",
        "카카오가 25년 첫 분기에 연결 기준으로 기록한 영업수익은 얼마인가?",
    ],
    "strong-close-002": [
        "기아 2025년 1Q 연결 기준 영업이익을 공시 근거와 함께 답해줘.",
        "25년 첫 분기 기아의 연결 영업이익 숫자는 얼마야?",
    ],
    "strong-close-003": [
        "NAVER가 2025년 첫 분기에 낸 연결 영업이익은 얼마인지 알려줘.",
        "네이버 25년 1Q 연결 손익계산서상 영업이익 수치를 찾아줘.",
    ],
    "strong-close-004": [
        "삼성전자 2025년 첫 분기 연결 당기순이익을 공시 단위로 답해줘.",
        "25년 1Q 삼성전자의 연결 순이익은 몇 백만원인가?",
    ],
    "strong-close-005": [
        "2025년 1분기 SK하이닉스 연결 매출과 영업이익으로 영업이익률을 산출해줘.",
        "SK하이닉스의 25년 1Q 연결 영업이익률은 얼마야? 계산 근거도 보여줘.",
    ],
    "strong-close-006": [
        "NAVER 2025년 1분기 연결 영업수익의 전년동기 대비 증가액과 증가율을 계산해줘.",
        "네이버 25년 첫 분기 영업수익은 전년 같은 분기보다 얼마, 몇 % 늘었나? 연결 기준으로 답해줘.",
    ],
    "strong-close-007": [
        "현대자동차와 기아의 2025년 1Q 연결 매출액 격차를 계산해줘.",
        "25년 첫 분기 연결 매출 기준으로 현대차가 기아보다 얼마 더 큰가?",
    ],
    "strong-close-008": [
        "2025년 삼성전자 반도체 위탁생산 계약금액이 최근 매출액에서 차지하는 비율은?",
        "2025년 삼성전자 위탁생산 공급계약 공시의 계약금액/최근매출액 비중을 알려줘.",
    ],
    "strong-close-009": [
        "현대차 2026년 1분기 공시에서 연간 투자계획 총액, 누적 집행액, 집행률을 답해줘.",
        "2026 1Q 현대자동차 투자계획은 얼마이고 1분기까지 얼마를 집행해 몇 %가 됐나?",
    ],
    "strong-close-010": [
        "삼성전자 2025년 반도체 위탁생산 계약에서 정정 후 공개된 상대방은 어디야?",
        "25년 삼성전자 위탁생산 공급계약의 최신 계약상대방 명칭을 알려줘.",
    ],
    "audit-close-001": [
        "LG이노텍이 2025년 첫 분기에 기록한 연결 매출액은 얼마야?",
        "25년 1Q LG이노텍 연결 손익계산서의 매출 수치를 답해줘.",
    ],
    "audit-close-002": [
        "LG이노텍의 25년 첫 분기 연결 영업이익을 알려줘.",
        "2025 1Q LG이노텍 연결 기준 영업이익은 몇 백만원인가?",
    ],
    "audit-close-003": [
        "삼성바이오로직스 2025년 첫 분기 연결 매출액을 원 단위로 답해줘.",
        "25년 1Q 삼성바이오로직스의 연결 매출은 얼마인가?",
    ],
    "audit-close-004": [
        "삼성바이오로직스가 2025년 1분기에 낸 연결 영업이익은 얼마야?",
        "25년 첫 분기 삼성바이오로직스 연결 영업이익 수치를 공시에서 찾아줘.",
    ],
    "audit-close-005": [
        "HMM 2025년 첫 분기 연결 매출액을 백만원 단위로 알려줘.",
        "25년 1Q HMM 연결 손익계산서상 매출은 얼마인가?",
    ],
    "audit-close-006": [
        "HMM이 2025년 1분기에 기록한 연결 영업이익은 얼마야?",
        "25년 첫 분기 HMM 연결 영업이익 수치를 답해줘.",
    ],
    "audit-close-007": [
        "HD현대일렉트릭의 2025년 첫 분기 연결 매출액은 얼마인지 알려줘.",
        "25년 1Q HD현대일렉트릭 연결 매출을 원 단위로 답해줘.",
    ],
    "audit-close-008": [
        "HD현대일렉트릭 2025년 1분기 연결 영업이익 수치는?",
        "25년 첫 분기 HD현대일렉트릭의 연결 영업이익을 공시 근거로 답해줘.",
    ],
    "audit-close-009": [
        "POSCO홀딩스가 2025년 1Q 기록한 연결 매출액은 얼마야?",
        "2025년 첫 분기 포스코홀딩스 연결 매출을 원 단위로 알려줘.",
    ],
    "audit-close-010": [
        "포스코홀딩스의 25년 1분기 연결 영업이익을 알려줘.",
        "POSCO홀딩스 2025년 첫 분기 연결 손익계산서상 영업이익은 얼마인가?",
    ],
    "audit-close-011": [
        "LG유플러스가 2025년 1Q 낸 연결 영업이익은 얼마야?",
        "25년 첫 분기 LG유플러스 연결 영업이익을 백만원 단위로 답해줘.",
    ],
    "audit-close-012": [
        "LG유플러스의 2025년 첫 분기 연결 당기순이익 수치는?",
        "25년 1Q LG유플러스 연결 순이익이 몇 백만원인지 알려줘.",
    ],
    "audit-close-013": [
        "CJ제일제당 2025년 첫 분기 연결 매출액을 공시 단위로 답해줘.",
        "25년 1Q CJ제일제당의 연결 매출은 얼마인가?",
    ],
    "audit-close-014": [
        "CJ제일제당이 2025년 1분기에 낸 연결 영업이익은 얼마야?",
        "25년 첫 분기 CJ제일제당 연결 영업이익 수치를 알려줘.",
    ],
    "audit-close-015": [
        "셀트리온의 2025년 첫 분기 연결 영업이익을 원 단위로 알려줘.",
        "25년 1Q 셀트리온 연결 손익계산서상 영업이익은 얼마인가?",
    ],
}


# Two independent phrasings per source case.  25 sources x 2 = 50 open cases.
OPEN_VARIANTS = {
    "strong-open-001": [
        "KT의 2025년 1Q 공시를 토대로 사업 포트폴리오와 대표 서비스를 분야별로 설명해줘.",
        "2025년 첫 분기 KT가 영위한 주요 사업과 서비스를 구조적으로 요약해줘.",
    ],
    "strong-open-002": [
        "엔씨소프트의 25년 1분기 공시에서 온라인·모바일 제품군과 대표 게임을 정리해줘.",
        "2025년 첫 분기 엔씨소프트의 핵심 서비스와 게임 사업 구성을 설명해줘.",
    ],
    "strong-open-003": [
        "현대자동차 2025년 1Q 차량부문의 현황과 중장기 방향을 공시 기준으로 설명해줘.",
        "25년 첫 분기 현대차 보고서에서 자동차 사업과 2030 전략의 핵심을 요약해줘.",
    ],
    "strong-open-004": [
        "삼성전자 2025년 1분기 각 사업부문이 취급하는 대표 제품을 구분해 설명해줘.",
        "25년 1Q 삼성전자의 DX·DS·SDC·Harman 사업 구성을 공시 근거로 요약해줘.",
    ],
    "strong-open-005": [
        "SK하이닉스의 2025년 첫 분기 주력 반도체 제품과 함께 운영하는 사업을 정리해줘.",
        "25년 1Q SK하이닉스 사업보고 내용에서 메모리 제품군과 기타 사업을 설명해줘.",
    ],
    "strong-open-006": [
        "카카오의 2025년 첫 분기 사업을 플랫폼과 콘텐츠 두 축으로 나눠 세부 항목을 정리해줘.",
        "25년 1Q 카카오 공시에서 플랫폼·콘텐츠 부문의 수익 사업을 비교해 설명해줘.",
    ],
    "strong-open-007": [
        "JYP Ent.가 2025년 1분기에 어떤 활동으로 수익을 창출했는지 사업별로 설명해줘.",
        "25년 첫 분기 JYP의 음반·매니지먼트·IP 중심 사업모델을 공시 기준으로 요약해줘.",
    ],
    "strong-open-008": [
        "NAVER의 2025년 첫 분기 서비스 포트폴리오를 다섯 가지 축으로 분류해 설명해줘.",
        "25년 1Q 네이버의 주요 사업 영역 5개와 각 영역의 성격을 요약해줘.",
    ],
    "strong-open-009": [
        "현대자동차 2023·2025 사업보고서를 대조해 유지된 사업과 새로 강조된 전략을 설명해줘.",
        "2023년에서 2025년 사이 현대차 사업 전략이 어떻게 달라졌는지 매출 비중 근거와 함께 분석해줘.",
    ],
    "strong-open-010": [
        "NAVER의 2025 분기보고서와 사업보고서에서 공통 사업 축과 강조점 차이를 비교해줘.",
        "2025년 네이버 분기보고서와 사업보고서를 대조해 핵심 서비스는 무엇이 같고 표현은 어떻게 달라졌는지 설명해줘.",
    ],
    "audit-open-001": [
        "HMM의 2025년 첫 분기 해운·물류 사업 구성과 추진 전략을 정리해줘.",
        "25년 1Q HMM 공시를 근거로 컨테이너·벌크 사업과 시장 대응 방향을 설명해줘.",
    ],
    "audit-open-002": [
        "삼성바이오로직스의 2025년 1분기 핵심 사업과 제공 제품·서비스를 요약해줘.",
        "25년 첫 분기 삼성바이오로직스가 수행한 CDMO 사업을 공시 근거로 설명해줘.",
    ],
    "audit-open-003": [
        "CJ제일제당의 2025년 첫 분기 사업 구성을 식품·바이오·물류 축으로 정리해줘.",
        "25년 1Q CJ제일제당의 주요 사업부문과 포트폴리오를 공시 기준으로 설명해줘.",
    ],
    "audit-open-004": [
        "LG유플러스의 2025년 1분기 사업을 모바일·스마트홈·기업인프라로 나눠 설명해줘.",
        "25년 첫 분기 LG유플러스가 제공한 주요 통신·플랫폼 서비스를 정리해줘.",
    ],
    "audit-open-005": [
        "크래프톤의 2025년 첫 분기 게임 사업과 성장 전략을 공시 내용으로 요약해줘.",
        "25년 1Q 크래프톤의 주요 게임 플랫폼과 사업 확장 방향을 설명해줘.",
    ],
    "audit-open-006": [
        "POSCO홀딩스의 2025년 1분기 사업 포트폴리오와 각 부문의 전략을 정리해줘.",
        "25년 첫 분기 포스코홀딩스의 철강·인프라·에너지소재 사업을 구분해 설명해줘.",
    ],
    "audit-open-007": [
        "LG이노텍의 2025년 첫 분기 사업부문별 주요 제품을 정리해줘.",
        "25년 1Q LG이노텍 공시에서 광학·기판소재·전장부품 사업을 설명해줘.",
    ],
    "audit-open-008": [
        "현대건설의 2025년 1분기 주요 건설 사업 영역을 공시 기준으로 요약해줘.",
        "25년 첫 분기 현대건설이 영위한 토목·건축·주택·에너지 관련 사업을 정리해줘.",
    ],
    "audit-open-009": [
        "셀트리온의 2025년 첫 분기 바이오 사업과 연구개발 방향을 설명해줘.",
        "25년 1Q 셀트리온의 주력 의약품 사업과 R&D 초점을 공시 근거로 요약해줘.",
    ],
    "audit-open-010": [
        "HD현대일렉트릭의 2025년 첫 분기 전력기기 사업과 주요 제품을 정리해줘.",
        "25년 1Q HD현대일렉트릭이 판매한 변압기·차단기 등 제품군을 설명해줘.",
    ],
    "manual-business-001": [
        "네이버 2025년 분기공시의 사업의 내용에서 핵심 서비스 포트폴리오를 설명해줘.",
        "2025년 네이버 분기보고서를 토대로 회사의 주요 사업을 분야별로 요약해줘.",
    ],
    "manual-business-002": [
        "현대차 2025년 분기공시에서 자동차 부문의 사업 내용을 중심으로 정리해줘.",
        "2025년 현대자동차 분기보고서가 설명하는 차량 관련 사업을 요약해줘.",
    ],
    "manual-business-003": [
        "KT의 2025년 분기공시에 나타난 대표 서비스들을 사업 영역별로 알려줘.",
        "2025년 KT 분기보고서 기준 주요 서비스 포트폴리오를 설명해줘.",
    ],
    "manual-business-004": [
        "엔씨소프트의 2025년 분기보고서에 기재된 핵심 제품과 서비스를 정리해줘.",
        "2025년 엔씨소프트 정기공시를 기준으로 주요 게임·서비스 사업을 설명해줘.",
    ],
    "manual-business-005": [
        "JYP의 2025년 분기공시에서 확인되는 주요 사업 활동을 요약해줘.",
        "2025년 JYP 분기보고서를 바탕으로 회사의 수익 사업을 정리해줘.",
    ],
}


def _load_sources() -> dict[str, dict]:
    sources: dict[str, dict] = {}
    for filename in (
        "strong_gold_questions.jsonl",
        "cross_industry_audit_questions.jsonl",
        "manual_qa_questions.jsonl",
    ):
        path = ROOT / "eval" / filename
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                case = json.loads(line)
                sources[case["question_id"]] = case
    return sources


def build_cases() -> list[dict]:
    sources = _load_sources()
    cases: list[dict] = []
    counters = {"close": 0, "open": 0}
    for category, variants_by_source in (("close", CLOSE_VARIANTS), ("open", OPEN_VARIANTS)):
        if len(variants_by_source) != 25:
            raise ValueError(f"{category} must have exactly 25 source cases")
        for source_id, questions in variants_by_source.items():
            if source_id not in sources:
                raise KeyError(f"unknown source case: {source_id}")
            if len(questions) != 2:
                raise ValueError(f"{source_id} must have exactly two variants")
            for question in questions:
                counters[category] += 1
                case = deepcopy(sources[source_id])
                case.update(
                    question_id=f"development-{category}-{counters[category]:03d}",
                    category=category,
                    format=category,
                    question=question,
                    source_case_id=source_id,
                    authorship="self_authored_semantic_variant",
                )
                cases.append(case)
    if counters != {"close": 50, "open": 50} or len(cases) != 100:
        raise AssertionError(f"unexpected case counts: {counters}, total={len(cases)}")
    normalized = {"".join(case["question"].split()).lower() for case in cases}
    if len(normalized) != len(cases):
        raise AssertionError("duplicate development question detected")
    return cases


def main() -> None:
    cases = build_cases()
    OUTPUT.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(OUTPUT), "total": len(cases), "close": 50, "open": 50}, ensure_ascii=False))


if __name__ == "__main__":
    main()
