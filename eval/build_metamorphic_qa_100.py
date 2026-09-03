"""Build 100 paired questions that test exclusion handling and answer invariance."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "eval" / "adversarial_qa_100_questions.jsonl"
OUTPUT = ROOT / "eval" / "metamorphic_qa_100_questions.jsonl"
DECOY_COMPANIES = ("삼성전자", "현대자동차", "NAVER", "카카오", "HMM", "LG이노텍", "기아")
CONSISTENCY_FIELDS = (
    "validation_action", "query_type", "plan_companies", "plan_years",
    "plan_quarter", "plan_metric", "plan_scope", "cited_doc_ids",
)


def _normalized(question: str) -> str:
    return re.sub(r"\s+", "", question).casefold()


def _decoy(expected_companies: list[str]) -> str:
    return next(company for company in DECOY_COMPANIES if company not in expected_companies)


def _load_existing_questions() -> set[str]:
    questions = set()
    for path in (ROOT / "eval").glob("*questions.jsonl"):
        if path == OUTPUT:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                questions.add(_normalized(json.loads(line)["question"]))
    return questions


def build_cases() -> list[dict]:
    source_cases = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(source_cases) != 100:
        raise AssertionError("adversarial source set must contain 100 cases")

    cases = []
    category_indexes = {"close": 0, "open": 0}
    pair_indexes = {"close": 0, "open": 0}
    for source in source_cases:
        category = source["format"]
        category_indexes[category] += 1
        item_index = category_indexes[category]
        variant = 1 if item_index % 2 else 2
        if variant == 1:
            pair_indexes[category] += 1
        pair_index = pair_indexes[category]
        expected_companies = source.get("expected_companies", [])
        decoy = _decoy(expected_companies)
        decoy_year = 2022
        if variant == 1:
            question = (
                f"[제외 조건: {decoy}, {decoy_year}년] {source['question']} "
                "앞의 제외 대상은 검색·비교·답변에 포함하지 마."
            )
        else:
            question = (
                f"{source['question']} (비교 금지={decoy}; 참조 금지={decoy_year}년). "
                "금지 항목 대신 원래 요청한 대상만 답해."
            )
        case = deepcopy(source)
        case.update({
            "question_id": f"metamorphic-{category}-{item_index:03d}",
            "question": question,
            "source_case_id": source["question_id"],
            "metamorphic_pair_id": f"metamorphic-{category}-pair-{pair_index:03d}",
            "metamorphic_variant": variant,
            "consistency_fields": list(CONSISTENCY_FIELDS),
            "expected_exact_companies": expected_companies,
            "expected_exact_years": source.get("expected_years", []),
            "excluded_companies": [decoy],
            "excluded_years": [decoy_year],
            "authorship": "self_authored_metamorphic_exclusion_variant",
        })
        cases.append(case)

    counts = {category: sum(case["format"] == category for case in cases) for category in ("close", "open")}
    if len(cases) != 100 or counts != {"close": 50, "open": 50}:
        raise AssertionError(f"expected close/open 50 each, got {counts}")
    normalized = {_normalized(case["question"]) for case in cases}
    if len(normalized) != 100:
        raise AssertionError("duplicate metamorphic question detected")
    if normalized & _load_existing_questions():
        raise AssertionError("exact question overlap with an existing evaluation set detected")
    groups = {case["metamorphic_pair_id"] for case in cases}
    if len(groups) != 50 or any(sum(case["metamorphic_pair_id"] == group for case in cases) != 2 for group in groups):
        raise AssertionError("each of the 50 metamorphic groups must contain exactly two questions")
    return cases


def main() -> None:
    cases = build_cases()
    OUTPUT.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "total": 100, "close": 50, "open": 50, "pairs": 50},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
