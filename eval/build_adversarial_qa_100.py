"""Build a stricter 100-question adversarial HTTP evaluation set."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "eval" / "adversarial_qa_100_questions.jsonl"

CLOSE_SOURCE_IDS = [
    *(f"strong-close-{index:03d}" for index in range(1, 11)),
    *(f"audit-close-{index:03d}" for index in range(1, 11)),
    *(f"audit-conflict-{index:03d}" for index in range(1, 6)),
]
OPEN_SOURCE_IDS = [
    *(f"strong-open-{index:03d}" for index in range(1, 11)),
    *(f"audit-open-{index:03d}" for index in range(1, 11)),
    *(f"manual-business-{index:03d}" for index in range(1, 6)),
]

COMPANY_EXPECTATIONS = {
    "strong-close-001": ["카카오"], "strong-close-002": ["기아"],
    "strong-close-003": ["NAVER"], "strong-close-004": ["삼성전자"],
    "strong-close-005": ["SK하이닉스"], "strong-close-006": ["NAVER"],
    "strong-close-007": ["현대자동차", "기아"], "strong-close-008": ["삼성전자"],
    "strong-close-009": ["현대자동차"], "strong-close-010": ["삼성전자"],
    "audit-close-001": ["LG이노텍"], "audit-close-002": ["LG이노텍"],
    "audit-close-003": ["삼성바이오로직스"], "audit-close-004": ["삼성바이오로직스"],
    "audit-close-005": ["HMM"], "audit-close-006": ["HMM"],
    "audit-close-007": ["HD현대일렉트릭"], "audit-close-008": ["HD현대일렉트릭"],
    "audit-close-009": ["POSCO홀딩스"], "audit-close-010": ["POSCO홀딩스"],
    "audit-conflict-001": ["HMM"], "audit-conflict-002": ["HMM"],
    "audit-conflict-003": ["LG이노텍"], "audit-conflict-004": ["LG이노텍"],
    "audit-conflict-005": ["POSCO홀딩스"],
    "strong-open-001": ["케이티"], "strong-open-002": ["NC"],
    "strong-open-003": ["현대자동차"], "strong-open-004": ["삼성전자"],
    "strong-open-005": ["SK하이닉스"], "strong-open-006": ["카카오"],
    "strong-open-007": ["JYP Ent"], "strong-open-008": ["NAVER"],
    "strong-open-009": ["현대자동차"], "strong-open-010": ["NAVER"],
    "audit-open-001": ["HMM"], "audit-open-002": ["삼성바이오로직스"],
    "audit-open-003": ["CJ제일제당"], "audit-open-004": ["LG유플러스"],
    "audit-open-005": ["크래프톤"], "audit-open-006": ["POSCO홀딩스"],
    "audit-open-007": ["LG이노텍"], "audit-open-008": ["현대건설"],
    "audit-open-009": ["셀트리온"], "audit-open-010": ["HD현대일렉트릭"],
    "manual-business-001": ["NAVER"], "manual-business-002": ["현대자동차"],
    "manual-business-003": ["케이티"], "manual-business-004": ["NC"],
    "manual-business-005": ["JYP Ent"],
}

QUESTION_ALIASES = (
    ("삼성바이오로직스", "Samsung Biologics"),
    ("HD현대일렉트릭", "HD Hyundai Electric"),
    ("POSCO홀딩스", "포스코홀딩스"),
    ("LG이노텍", "LG Innotek"),
    ("LG유플러스", "LG U+"),
    ("CJ제일제당", "CJ CheilJedang"),
    ("현대자동차", "현차(005380)"),
    ("삼성전자", "삼성 전자(005930)"),
    ("SK하이닉스", "sk hynix(000660)"),
    ("엔씨소프트", "NC(036570)"),
    ("JYP Ent.", "jyp(035900)"),
    ("NAVER", "035420(NAVER)"),
    ("네이버", "035420(NAVER)"),
    ("카카오", "kakao(035720)"),
    ("기아", "KIA(000270)"),
    ("KT", "kt(030200)"),
)

METRIC_EXPECTATIONS = {
    "strong-close-001": "revenue", "strong-close-002": "operating_profit",
    "strong-close-003": "operating_profit", "strong-close-004": "net_income",
    "strong-close-005": "operating_margin", "strong-close-006": "revenue",
    "strong-close-007": "revenue", "strong-close-008": "contract_ratio",
    "audit-close-001": "revenue", "audit-close-002": "operating_profit",
    "audit-close-003": "revenue", "audit-close-004": "operating_profit",
    "audit-close-005": "revenue", "audit-close-006": "operating_profit",
    "audit-close-007": "revenue", "audit-close-008": "operating_profit",
    "audit-close-009": "revenue", "audit-close-010": "operating_profit",
    "audit-conflict-001": "revenue", "audit-conflict-002": "revenue",
    "audit-conflict-003": "operating_profit", "audit-conflict-004": "operating_profit",
    "audit-conflict-005": "revenue",
}


def _load_sources() -> dict[str, dict]:
    sources: dict[str, dict] = {}
    for filename in (
        "strong_gold_questions.jsonl", "cross_industry_audit_questions.jsonl", "manual_qa_questions.jsonl",
    ):
        for line in (ROOT / "eval" / filename).read_text(encoding="utf-8").splitlines():
            if line.strip():
                case = json.loads(line)
                sources[case["question_id"]] = case
    return sources


def _alias_and_compact(question: str) -> str:
    transformed = question
    for original, alias in QUESTION_ALIASES:
        transformed = transformed.replace(original, alias)
    transformed = transformed.replace("2025년 1분기", "'25 Q1").replace("2025년 3분기", "'25 Q3")
    transformed = transformed.replace("2026년 1분기", "'26 Q1")
    return transformed.rstrip(" ?")


def _strict_case(source: dict, category: str, index: int, variant: int) -> dict:
    source_id = source["question_id"]
    base = source["question"].strip()
    if variant == 1:
        core = _alias_and_compact(base)
        question = (f"[교차검증] {core}. 표기 흔들림에 속지 말고 공시 수치·단위·근거 접수번호를 "
                    f"빠짐없이 답해줘." if category == "close" else
                    f"[교차검증] {core}. 공시의 'II. 사업의 내용'만 근거로 핵심을 항목별로 답해줘.")
    else:
        question = (f"정답을 추측하지 마. {base.rstrip(' ?')}. 해당 기간 공시와 원문 기준을 확인하고 "
                    f"수치·단위·접수번호를 10줄 안에 제시해." if category == "close" else
                    f"외부 지식과 표지 문구는 제외해. {base.rstrip(' ?')}. 제품·서비스·전략을 구분하고 "
                    f"근거 접수번호를 포함해 8줄 이내로 정리해.")
    case = deepcopy(source)
    case.update({
        "question_id": f"adversarial-{category}-{index:03d}",
        "category": category,
        "format": category,
        "question": question,
        "source_case_id": source_id,
        "authorship": "self_authored_adversarial_variant",
        "expected_validation_action": "allow",
        "expected_companies": COMPANY_EXPECTATIONS[source_id],
        "required_citation_fields": ["doc_id", "corp_name", "report_nm", "rcept_no", "record_id", "kind"],
        "max_answer_chars": 800 if category == "close" else 900,
        "max_answer_lines": 10 if category == "close" else 8,
    })
    if category == "open" and not case.get("expected_query_type"):
        case["expected_query_type"] = "business_overview"
    if source_id in METRIC_EXPECTATIONS:
        case["expected_metric"] = METRIC_EXPECTATIONS[source_id]
    if source_id.startswith("audit-conflict-"):
        case["expected_years"] = [2025]
        case["expected_quarter"] = 3 if source_id != "audit-conflict-005" else 1
    elif source_id == "strong-close-009":
        case["expected_years"] = [2026]
        case["expected_quarter"] = 1
    elif source_id == "strong-open-009":
        case["expected_years"] = [2023, 2025]
    else:
        case["expected_years"] = [2025]
        if source_id.startswith(("strong-close-", "audit-close-", "strong-open-", "audit-open-")):
            if source_id not in {"strong-close-008", "strong-close-010", "strong-open-009", "strong-open-010"}:
                case["expected_quarter"] = 1
    return case


def build_cases() -> list[dict]:
    sources = _load_sources()
    cases: list[dict] = []
    for category, source_ids in (("close", CLOSE_SOURCE_IDS), ("open", OPEN_SOURCE_IDS)):
        if len(source_ids) != 25:
            raise AssertionError(f"{category} requires 25 audited sources")
        index = 0
        for source_id in source_ids:
            for variant in (1, 2):
                index += 1
                cases.append(_strict_case(sources[source_id], category, index, variant))
    if len(cases) != 100:
        raise AssertionError(f"expected 100 cases, got {len(cases)}")
    normalized = {re.sub(r"\s+", "", case["question"]).casefold() for case in cases}
    if len(normalized) != 100:
        raise AssertionError("duplicate adversarial question detected")
    existing_questions = set()
    for filename in (
        "development_qa_100_questions.jsonl", "strong_gold_questions.jsonl",
        "cross_industry_audit_questions.jsonl", "manual_qa_questions.jsonl",
    ):
        for line in (ROOT / "eval" / filename).read_text(encoding="utf-8").splitlines():
            if line.strip():
                question = json.loads(line)["question"]
                existing_questions.add(re.sub(r"\s+", "", question).casefold())
    if normalized & existing_questions:
        raise AssertionError("exact question overlap with an existing evaluation set detected")
    return cases


def main() -> None:
    cases = build_cases()
    OUTPUT.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "total": 100, "close": 50, "open": 50}, ensure_ascii=False))


if __name__ == "__main__":
    main()
