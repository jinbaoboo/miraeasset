"""Build 100 typo, spacing, shorthand, colloquial, and long-context questions."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "eval" / "noisy_language_qa_100_questions.jsonl"
CITATION_FIELDS = ["doc_id", "corp_name", "report_nm", "rcept_no", "record_id", "kind"]
LONG_PREFIX = (
    "내부 검토 메모를 작성 중인데 숫자를 빨리 보고 결론내리려는 목적은 아니고, "
    "공시 원문에서 회사·기간·연결 여부·단위를 정확히 맞췄는지 재검토하려는 요청이야. "
    "배경 설명은 검색 조건으로 쓰지 말고 핵심 질문만 처리해줘. "
)


SEEDS = [
    {
        "name": "samsung_revenue", "companies": ["삼성전자"], "years": [2025],
        "questions": [
            "삼섬전자 2025년 1분기 연결 매출액 얼마야?",
            "삼전 25년 1Q 연결 매출, 딱 얼마 나왔어?",
            LONG_PREFIX + "삼성전자 2025년 1분기 연결 매출액을 알려줘.",
            "삼 성 전 자 '25 첫 분기 연결 매출액 좀 봐줘.",
            "005930의 2025년 1사분기 연결 매출은?",
        ],
        "query_type": "financial_metric", "numeric": [(79140503, 0)], "units": ["백만원"],
        "scopes": ["연결"], "periods": ["2025년 1분기"], "contains": ["삼성전자", "매출"],
        "docs": ["periodic_20250515001922"],
    },
    {
        "name": "naver_profit", "companies": ["NAVER"], "years": [2025],
        "questions": [
            "네이바 2025년 1분기 연결 영업이익 얼마야?",
            "네이버 25년 1Q 연결 영업익 좀 알려줘.",
            LONG_PREFIX + "NAVER의 2025년 1분기 연결 영업이익을 확인해줘.",
            "N A V E R 2025년 첫 분기 연결 영업 이익은?",
            "035420, 2025년 1/4분기 연결 영업이익 얼마?",
        ],
        "query_type": "financial_metric", "numeric": [(505301333937, 0)], "units": ["원"],
        "scopes": ["연결"], "periods": ["2025년 1분기"], "contains": ["NAVER", "영업이익"],
        "docs": ["periodic_20250515001302"],
    },
    {
        "name": "sk_margin", "companies": ["SK하이닉스"], "years": [2025],
        "questions": [
            "SK하이닉쓰 2025년 1분기 연결 영업이익률 계산해줘.",
            "하이닉스 25년 1Q 연결 영업익률 몇 프로야?",
            LONG_PREFIX + "SK하이닉스의 2025년 1분기 연결 영업이익률을 계산해줘.",
            "S K 하 이 닉 스 2025년 첫 분기 연결 영업이익률은?",
            "000660의 2025년 1사분기 연결 영업이익률 계산.",
        ],
        "query_type": "financial_metric", "numeric": [(42.18, 0.02)], "units": ["%"],
        "scopes": ["연결"], "periods": ["2025년 1분기"], "contains": ["SK하이닉스", "영업이익률"],
        "docs": ["periodic_20250515002103"],
    },
    {
        "name": "hyundai_revenue", "companies": ["현대자동차"], "years": [2025],
        "questions": [
            "현대자돟차 2025년 1분기 연결 매출액은?",
            "현차 25년 1Q 연결 매출 얼마 찍혔어?",
            LONG_PREFIX + "현대자동차 2025년 1분기 연결 매출액을 제시해줘.",
            "현 대 자 동 차 2025년 첫 분기 연결 매출액은?",
            "005380의 2025년 1/4분기 연결 매출은?",
        ],
        "query_type": "financial_metric", "numeric": [(44407761, 0)], "units": ["백만원"],
        "scopes": ["연결"], "periods": ["2025년 1분기"], "contains": ["현대자동차", "매출"],
        "docs": ["periodic_20250515001159"],
    },
    {
        "name": "hmm_profit", "companies": ["HMM"], "years": [2025],
        "questions": [
            "에이치엠엠 2025년 1분기 연결 영업이익은?",
            "hmm 25년 1Q 연결 영업익 얼마야?",
            LONG_PREFIX + "HMM 2025년 1분기 연결 영업이익을 알려줘.",
            "H M M 2025년 첫 분기 연결 영업 이익은?",
            "011200의 2025년 1사분기 연결 영업이익은?",
        ],
        "query_type": "financial_metric", "numeric": [(613899, 0)], "units": ["백만원"],
        "scopes": ["연결"], "periods": ["2025년 1분기"], "contains": ["HMM", "영업이익"],
        "docs": ["periodic_20250514000594"],
    },
    {
        "name": "jyp_revenue", "companies": ["JYP Ent"], "years": [2025],
        "questions": [
            "제이와피엔터 2025년 1분기 연결 매출액은?",
            "jyp 25년 1Q 연결 매출 얼마야?",
            LONG_PREFIX + "JYP Ent. 2025년 1분기 연결 매출액을 알려줘.",
            "J Y P Ent 2025년 첫 분기 연결 매출액은?",
            "035900의 2025년 1/4분기 연결 매출은?",
        ],
        "query_type": "financial_metric", "numeric": [(140759310563, 0)], "units": ["원"],
        "scopes": ["연결"], "periods": ["2025년 1분기"], "contains": ["JYP Ent", "매출"],
        "docs": ["periodic_20250514000990"],
    },
    {
        "name": "kakao_revenue", "companies": ["카카오"], "years": [2025],
        "questions": [
            "카까오 2025년 1분기 연결 영업수익은?",
            "kakao 25년 1Q 연결 매출 얼마 나왔어?",
            LONG_PREFIX + "카카오 2025년 1분기 연결 영업수익을 알려줘.",
            "카 카 오 2025년 첫 분기 연결 영업 수익은?",
            "035720의 2025년 1사분기 연결 영업수익은?",
        ],
        "query_type": "financial_metric", "numeric": [(1863730780367, 0)], "units": ["원"],
        "scopes": ["연결"], "periods": ["2025년 1분기"], "contains": ["카카오"],
        "contains_any": ["영업수익", "매출"],
        "docs": ["periodic_20250514001239"],
    },
    {
        "name": "samsung_overview", "companies": ["삼성전자"], "years": [2025],
        "questions": [
            "삼섬전자 2025년 1분기 보고서 주요 사업이랑 대표 제품 정리해줘.",
            "삼전 25년 1Q에 뭐로 돈 버는지 사업부문별로 풀어줘.",
            LONG_PREFIX + "삼성전자 2025년 1분기 분기보고서의 주요 사업부문과 대표 제품을 정리해줘.",
            "삼 성 전 자 2025년 첫 분기 주요 사업 부문은 뭐야?",
            "005930의 2025년 1사분기 분기보고서 주요 사업과 제품은?",
        ],
        "query_type": "business_overview", "contains": ["삼성전자", "DX", "DS", "DRAM", "NAND"],
        "docs": ["periodic_20250515001922"],
    },
    {
        "name": "samsung_contract_current", "companies": ["삼성전자"], "years": [2025],
        "questions": [
            "삼섬전자 2025년 위탁생산 공급계약 지금 계약상대가 누구야?",
            "삼전 25년 파운드리 계약 상대, 정정 끝난 최신값 누구임?",
            LONG_PREFIX + "삼성전자 2025년 반도체 위탁생산 공급계약의 현재 유효 계약상대를 알려줘.",
            "삼 성 전 자 2025년 공급 계약 현재 유효한 계약 상대는?",
            "005930이 2025년에 공시한 위탁생산 공급계약의 최신 계약상대는?",
        ],
        "query_type": "correction_history", "contains": ["현재 유효", "테슬라", "접수번호"],
        "docs": ["exchange_20250728800035", "exchange_20250731800028"],
    },
    {
        "name": "lg_innotek_correction", "companies": ["LG이노텍"], "years": [2024],
        "questions": [
            "엘지이노택 2024년 반기보고서 정정 전후 값 알려줘.",
            "엘지이노텍 24년 반기 정정된 거, 전이랑 후가 어떻게 달라?",
            LONG_PREFIX + "LG이노텍 2024년 반기보고서 정정 전후와 현재 유효 값을 구분해줘.",
            "L G 이 노 텍 2024년 반기 보고서 정정 전 후 값은?",
            "011070의 2024년 반기보고서 정정 내역과 현재 값은?",
        ],
        "query_type": "correction_history", "contains": ["정정 전", "정정 후", "1,223,131", "534,414"],
        "docs": ["periodic_20240814002436", "periodic_20240913000803"],
    },
]


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def build_cases() -> list[dict]:
    cases = []
    for category in ("close", "open"):
        index = 0
        for seed in SEEDS:
            for variant, question in enumerate(seed["questions"], start=1):
                index += 1
                case = {
                    "question_id": f"noisy-{category}-{index:03d}",
                    "category": category, "format": category,
                    "question": f"[{'판정형' if category == 'close' else '서술형'}] {question}",
                    "source_case_id": seed["name"], "variant": variant,
                    "authorship": "self_authored_noisy_language_stress",
                    "expected_query_type": seed["query_type"],
                    "expected_answerability": "answerable",
                    "expected_validation_actions": ["allow"],
                    "expected_companies": seed["companies"],
                    "expected_exact_companies": seed["companies"],
                    "expected_years": seed["years"],
                    "expected_exact_years": seed["years"],
                    "expected_numeric": [
                        {"value": value, "tolerance": tolerance}
                        for value, tolerance in seed.get("numeric", [])
                    ],
                    "expected_units": seed.get("units", []),
                    "expected_scopes": seed.get("scopes", []),
                    "expected_periods": seed.get("periods", []),
                    "expected_answer_contains": seed["contains"],
                    "expected_answer_any": seed.get("contains_any", []),
                    "required_doc_ids": seed["docs"],
                    "required_citation_fields": CITATION_FIELDS,
                    "max_answer_chars": 1800,
                    "max_answer_lines": 15,
                }
                cases.append(case)
    if len(cases) != 100:
        raise AssertionError(f"expected 100 cases, got {len(cases)}")
    if len({_normalized(case["question"]) for case in cases}) != 100:
        raise AssertionError("duplicate noisy-language question detected")
    existing = set()
    for path in (ROOT / "eval").glob("*questions.jsonl"):
        if path == OUTPUT:
            continue
        existing.update(_normalized(json.loads(line)["question"])
                        for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if {_normalized(case["question"]) for case in cases} & existing:
        raise AssertionError("exact overlap with existing questions")
    return cases


def main() -> None:
    cases = build_cases()
    OUTPUT.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "total": 100, "close": 50, "open": 50}, ensure_ascii=False))


if __name__ == "__main__":
    main()
