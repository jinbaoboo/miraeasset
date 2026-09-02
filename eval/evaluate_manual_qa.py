"""Evaluate manually authored QA expectations against the local Agent."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List

from src.agent.disclosure_agent import DisclosureAgent


def _normalized(value: str) -> str:
    return "".join((value or "").split()).lower()


def _context_audit_text(response: Dict[str, Any]) -> str:
    contexts = response.get("retrieved_context") or []
    plan = (response.get("think_trace") or {}).get("query_plan") or {}
    lines: List[str] = []
    for company in plan.get("companies") or []:
        stock = f" ({company.get('stock_code')})" if company.get("stock_code") else ""
        lines.append(f"[company] {company.get('corp_name')}{stock}")
    for item in contexts:
        lines.append(item.get("content") or "")
        lines.append(json.dumps(item.get("citation") or {}, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


LIMIT_MARKERS = (
    "확인할 수 없습니다", "찾지 못했습니다", "근거를 찾지 못했습니다",
    "자료가 필요", "구체적으로 입력", "질문 조건에 해당", "보안상", "질문이 비어",
)


def _numbers(text: str) -> List[Decimal]:
    """Return display numbers without guessing their source unit.

    Unit/scope/period are checked separately.  This intentionally validates the
    number shown to a user rather than a binary floating-point field in SQLite.
    """
    values: List[Decimal] = []
    for token in re.findall(r"(?<![0-9A-Za-z])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text or ""):
        try:
            values.append(Decimal(token.replace(",", "")))
        except InvalidOperation:
            continue
    return values


def _numeric_expectations(answer: str, expectations: List[Dict[str, Any]]) -> tuple[bool, List[Dict[str, Any]]]:
    actual = _numbers(answer)
    details: List[Dict[str, Any]] = []
    passed = True
    for expectation in expectations:
        target = Decimal(str(expectation["value"]))
        tolerance = Decimal(str(expectation.get("tolerance", 0)))
        matched = next((value for value in actual if abs(value - target) <= tolerance), None)
        ok = matched is not None
        passed = passed and ok
        details.append({"target": str(target), "tolerance": str(tolerance),
                        "matched": str(matched) if matched is not None else None, "passed": ok})
    return passed, details


def _answerability_check(answer: str, response: Dict[str, Any], expected: str | None) -> bool:
    if not expected:
        return True
    action = (response.get("validation") or {}).get("action")
    limited = any(marker in answer for marker in LIMIT_MARKERS)
    if expected == "answerable":
        return not limited and action == "allow"
    if expected == "unanswerable":
        return limited and action in {"limit", "clarify", "blocked"}
    if expected == "clarify":
        return action == "clarify"
    return False


def _effective_plan(response: Dict[str, Any]) -> Dict[str, Any]:
    """Return the analyzer plan for both single and composite questions."""
    plan = (response.get("think_trace") or {}).get("query_plan") or {}
    return plan.get("base_plan") or plan


def evaluate(db: Path, questions: Path, output: Path) -> Dict[str, Any]:
    cases = [json.loads(line) for line in questions.read_text(encoding="utf-8").splitlines() if line.strip()]
    agent = DisclosureAgent(db); results = []
    try:
        for case in cases:
            started = time.perf_counter()
            response = agent.answer(case["question_id"], case["question"], use_llm=False)
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            answer = response.get("answer") or ""
            context_text = _context_audit_text(response)
            raw_plan = (response.get("think_trace") or {}).get("query_plan") or {}
            effective_plan = _effective_plan(response)
            query_type = effective_plan.get("query_type")
            cited_doc_ids = {item.get("doc_id") for item in response.get("citations") or [] if item.get("doc_id")}
            cited_doc_ids.update(
                item.get("citation", {}).get("doc_id")
                for item in response.get("retrieved_context") or []
                if item.get("citation", {}).get("doc_id")
            )
            numeric_ok, numeric_details = _numeric_expectations(answer, case.get("expected_numeric", []))
            required_evidence = case.get("required_evidence", case.get("expected_context_contains", []))
            required_doc_ids = set(case.get("required_doc_ids", []))
            max_chars = case.get("max_answer_chars")
            max_lines = case.get("max_answer_lines")
            expected_actions = case.get("expected_validation_actions") or (
                [case["expected_validation_action"]] if case.get("expected_validation_action") else []
            )
            plan_companies = {item.get("corp_name") for item in effective_plan.get("companies") or []}
            limitation_codes = {item.get("code") for item in response.get("limitations") or []}
            required_citation_fields = case.get("required_citation_fields", [])
            citations = response.get("citations") or []
            checks = {
                "required_evidence": all(_normalized(expected) in _normalized(context_text)
                                         for expected in required_evidence),
                "answer_contains": all(_normalized(expected) in _normalized(answer)
                                       for expected in case.get("expected_answer_contains", [])),
                "answer_contains_any": not case.get("expected_answer_any") or any(
                    _normalized(expected) in _normalized(answer)
                    for expected in case.get("expected_answer_any", [])
                ),
                "answer_excludes": all(_normalized(expected) not in _normalized(answer)
                                       for expected in case.get("expected_answer_not_contains", [])),
                "query_type": not case.get("expected_query_type") or query_type == case["expected_query_type"],
                "numeric_tolerance": numeric_ok,
                "unit": all(_normalized(unit) in _normalized(answer) for unit in case.get("expected_units", [])),
                "scope": all(_normalized(scope) in _normalized(answer) for scope in case.get("expected_scopes", [])),
                "period": all(_normalized(period) in _normalized(answer) for period in case.get("expected_periods", [])),
                "required_citation_docs": not required_doc_ids or required_doc_ids.issubset(cited_doc_ids),
                "answer_citation": not case.get("require_answer_citation", case.get("category") in {"close", "open"}) or (
                    "접수번호" in answer and bool(response.get("citations"))
                ),
                "answerability": _answerability_check(answer, response, case.get("expected_answerability")),
                "answer_length": (max_chars is None or len(answer) <= int(max_chars)) and (
                    max_lines is None or len(answer.splitlines()) <= int(max_lines)
                ),
                "guardrail_action": (response.get("validation") or {}).get("action") in
                                    {"allow", "limit", "clarify", "blocked"},
                "expected_validation_action": not expected_actions or
                    (response.get("validation") or {}).get("action") in expected_actions,
                "plan_companies": all(company in plan_companies for company in case.get("expected_companies", [])),
                "plan_years": all(year in (effective_plan.get("years") or []) for year in case.get("expected_years", [])),
                "plan_quarter": case.get("expected_quarter") is None or
                    effective_plan.get("quarter") == case.get("expected_quarter"),
                "plan_metric": not case.get("expected_metric") or
                    effective_plan.get("metric") == case.get("expected_metric"),
                "plan_doc_subtypes": all(
                    subtype in (effective_plan.get("doc_subtypes") or [])
                    for subtype in case.get("expected_doc_subtypes", [])
                ),
                "limitation_codes": all(code in limitation_codes for code in case.get("expected_limitation_codes", [])),
                "composite_subtasks": case.get("expected_subtask_count") is None or (
                    raw_plan.get("is_composite") is True and
                    len(raw_plan.get("subtasks") or []) == int(case["expected_subtask_count"])
                ),
                "citation_fields": not required_citation_fields or bool(citations) and all(
                    all(citation.get(field) not in (None, "") for field in required_citation_fields)
                    for citation in citations
                ),
            }
            results.append({
                "question_id": case["question_id"], "category": case["category"], "question": case["question"],
                "passed": all(checks.values()), "checks": checks, "latency_ms": latency_ms,
                "validation_action": (response.get("validation") or {}).get("action"),
                "query_type": query_type,
                "numeric_details": numeric_details,
                "cited_doc_ids": sorted(cited_doc_ids),
                "answer_chars": len(answer), "answer_lines": len(answer.splitlines()),
                "answer": answer, "notes": case.get("notes"),
                "gold_note": case.get("gold_note"),
            })
    finally:
        agent.close()
    by_category: Dict[str, Counter] = {}
    for row in results:
        by_category.setdefault(row["category"], Counter())["pass" if row["passed"] else "fail"] += 1
    latencies = [row["latency_ms"] for row in results]
    payload = {
        "total": len(results), "passed": sum(row["passed"] for row in results),
        "failed": sum(not row["passed"] for row in results),
        "by_category": {key: dict(value) for key, value in by_category.items()},
        "latency_ms": {"median": round(statistics.median(latencies), 2) if latencies else None,
                       "max": max(latencies) if latencies else None},
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--questions", type=Path, default=Path("eval/manual_qa_questions.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("eval/manual_qa_results.json"))
    args = parser.parse_args()
    result = evaluate(args.db, args.questions, args.output)
    print(json.dumps({key: result[key] for key in ("total", "passed", "failed", "by_category", "latency_ms")},
                     ensure_ascii=False, indent=2))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
