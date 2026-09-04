"""Build 100 deterministic multi-metric and calculation stress questions."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "eval" / "composite_calculation_qa_100_questions.jsonl"
CITATION_FIELDS = ["doc_id", "corp_name", "report_nm", "rcept_no", "record_id", "kind"]


SEEDS = [
    {
        "name": "samsung_three_metrics", "companies": ["삼성전자"], "years": [2025],
        "question": "삼성전자 2025년 1분기 연결 매출액, 영업이익, 당기순이익을 한 번에 알려줘.",
        "query_type": "financial_metric", "subtasks": 3,
        "numeric": [(79140503, 0), (6685272, 0), (8222878, 0)],
        "units": ["백만원"], "scopes": ["연결"], "periods": ["2025년 1분기"],
        "contains": ["매출액", "영업이익", "당기순이익"],
        "docs": ["periodic_20250515001922"],
    },
    {
        "name": "naver_two_metrics", "companies": ["NAVER"], "years": [2025],
        "question": "NAVER 2025년 1분기 연결 영업수익과 영업이익을 함께 계산 근거까지 알려줘.",
        "query_type": "financial_metric", "subtasks": 2,
        "numeric": [(2786783351907, 0), (505301333937, 0)],
        "units": ["원"], "scopes": ["연결"], "periods": ["2025년 1분기"],
        "contains": ["영업수익", "영업이익"], "docs": ["periodic_20250515001302"],
    },
    {
        "name": "hmm_two_metrics", "companies": ["HMM"], "years": [2025],
        "question": "HMM 2025년 1분기 연결 매출액과 영업이익을 같은 기준으로 같이 보여줘.",
        "query_type": "financial_metric", "subtasks": 2,
        "numeric": [(2854682, 0), (613899, 0)], "units": ["백만원"],
        "scopes": ["연결"], "periods": ["2025년 1분기"],
        "contains": ["매출액", "영업이익"], "docs": ["periodic_20250514000594"],
    },
    {
        "name": "kia_two_metrics", "companies": ["기아"], "years": [2025],
        "question": "기아 2025년 1분기 연결 매출액과 영업이익을 나란히 제시해줘.",
        "query_type": "financial_metric", "subtasks": 2,
        "numeric": [(28017510, 0), (3008588, 0)], "units": ["백만원"],
        "scopes": ["연결"], "periods": ["2025년 1분기"],
        "contains": ["매출액", "영업이익"], "docs": ["periodic_20250515001947"],
    },
    {
        "name": "sk_two_metrics", "companies": ["SK하이닉스"], "years": [2025],
        "question": "SK하이닉스 2025년 1분기 연결 매출액과 영업이익을 동일 보고서 기준으로 알려줘.",
        "query_type": "financial_metric", "subtasks": 2,
        "numeric": [(17639141, 0), (7440504, 0)], "units": ["백만원"],
        "scopes": ["연결"], "periods": ["2025년 1분기"],
        "contains": ["매출액", "영업이익"], "docs": ["periodic_20250515002103"],
    },
    {
        "name": "naver_yoy_amount_rate", "companies": ["NAVER"], "years": [2025],
        "question": "NAVER의 2025년 1분기 연결 영업수익이 전년 동기보다 얼마나 늘었는지 증감액과 증감률을 모두 계산해줘.",
        "query_type": "financial_metric",
        "numeric": [(260727936031, 0), (10.32, 0.02)], "units": ["원", "%"],
        "scopes": ["연결"], "periods": ["2025년 1분기"],
        "contains": ["영업수익", "증가", "증감률"], "docs": ["periodic_20250515001302"],
    },
    {
        "name": "sk_operating_margin", "companies": ["SK하이닉스"], "years": [2025],
        "question": "SK하이닉스 2025년 1분기 연결 매출액과 영업이익으로 영업이익률을 계산해줘.",
        "query_type": "financial_metric", "numeric": [(42.18, 0.02)], "units": ["%"],
        "scopes": ["연결"], "periods": ["2025년 1분기"],
        "contains": ["17,639,141", "7,440,504", "영업이익률"],
        "docs": ["periodic_20250515002103"],
    },
    {
        "name": "hyundai_kia_difference", "companies": ["현대자동차", "기아"], "years": [2025],
        "question": "현대자동차와 기아의 2025년 1분기 연결 매출액 차이를 계산하고 더 큰 회사를 판정해줘.",
        "query_type": "financial_metric",
        "numeric": [(44407761, 0), (28017510, 0), (16390251, 0)], "units": ["백만원"],
        "scopes": ["연결"], "periods": ["2025년 1분기"],
        "contains": ["현대자동차", "기아", "더 큽니다"],
        "docs": ["periodic_20250515001159", "periodic_20250515001947"],
    },
    {
        "name": "lg_energy_capex_strategy", "companies": ["LG에너지솔루션"], "years": [2023, 2025],
        "question": "LG에너지솔루션의 2023년과 2025년 설비투자를 비교하고 증감률과 주요 투자 방향을 설명해줘.",
        "query_type": "business_change", "subtasks": 2,
        "numeric": [(9923051, 0), (10833917, 0), (910866, 0), (9.18, 0.02)],
        "units": ["백만원", "%"], "contains": ["설비투자 증감률", "주요 투자 방향", "증가"],
        "docs": ["periodic_20240314001110", "periodic_20260312000217"],
    },
    {
        "name": "samsung_sdi_capex_strategy", "companies": ["삼성SDI"], "years": [2023, 2025],
        "question": "삼성SDI의 2023년과 2025년 설비투자를 비교하고 증감률과 주요 투자 방향을 설명해줘.",
        "query_type": "business_change", "subtasks": 2,
        "numeric": [(4048246715878, 0), (3066850926743, 0), (981395789135, 0), (24.24, 0.02)],
        "units": ["원", "%"], "contains": ["설비투자 증감률", "주요 투자 방향", "감소"],
        "docs": ["periodic_20240312000853", "periodic_20260310002954"],
    },
]


WRAPPERS = (
    lambda q: f"[복합계산 감사] {q} 각 결과의 원자료·단위·접수번호를 빠뜨리지 마.",
    lambda q: f"{q} 원자료 → 산식 → 결론 순서로 검산 가능하게 답해.",
    lambda q: f"계산요청={{ {q} }}; 서로 다른 기간·회사·단위를 섞지 마.",
    lambda q: f"{q} 중간값은 자르지 말고 반올림은 최종 표시 단계에서만 해.",
    lambda q: f"실무 검산이야. {q} 모든 하위 요구사항을 누락 없이 처리해줘.",
)


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def build_cases() -> list[dict]:
    cases = []
    for category in ("close", "open"):
        index = 0
        for seed in SEEDS:
            for variant, wrapper in enumerate(WRAPPERS, start=1):
                index += 1
                case = {
                    "question_id": f"composite-{category}-{index:03d}",
                    "category": category, "format": category,
                    "question": f"[{'판정형' if category == 'close' else '서술형'}] {wrapper(seed['question'])}",
                    "source_case_id": seed["name"], "variant": variant,
                    "authorship": "self_authored_composite_calculation_stress",
                    "expected_query_type": seed["query_type"],
                    "expected_answerability": "answerable",
                    "expected_validation_actions": ["allow"],
                    "expected_companies": seed["companies"],
                    "expected_exact_companies": seed["companies"],
                    "expected_years": seed["years"],
                    "expected_exact_years": seed["years"],
                    "expected_numeric": [
                        {"value": value, "tolerance": tolerance}
                        for value, tolerance in seed["numeric"]
                    ],
                    "expected_units": seed["units"],
                    "expected_scopes": seed.get("scopes", []),
                    "expected_periods": seed.get("periods", []),
                    "expected_answer_contains": seed["contains"],
                    "required_doc_ids": seed["docs"],
                    "required_citation_fields": CITATION_FIELDS,
                    "max_answer_chars": 5000,
                    "max_answer_lines": 45,
                }
                if seed.get("subtasks"):
                    case["expected_subtask_count"] = seed["subtasks"]
                cases.append(case)
    if len(cases) != 100:
        raise AssertionError(f"expected 100 cases, got {len(cases)}")
    if len({_normalized(case["question"]) for case in cases}) != 100:
        raise AssertionError("duplicate composite calculation question detected")
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
