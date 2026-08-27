"""Evaluate manually authored QA expectations against the local Agent."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
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
            checks = {
                "context_contains": all(_normalized(expected) in _normalized(context_text)
                                        for expected in case.get("expected_context_contains", [])),
                "answer_contains": all(_normalized(expected) in _normalized(answer)
                                       for expected in case.get("expected_answer_contains", [])),
                "answer_excludes": all(_normalized(expected) not in _normalized(answer)
                                       for expected in case.get("expected_answer_not_contains", [])),
                "guardrail_action": (response.get("validation") or {}).get("action") in {"allow", "limit", "clarify"},
            }
            results.append({
                "question_id": case["question_id"], "category": case["category"], "question": case["question"],
                "passed": all(checks.values()), "checks": checks, "latency_ms": latency_ms,
                "validation_action": (response.get("validation") or {}).get("action"),
                "query_type": ((response.get("think_trace") or {}).get("query_plan") or {}).get("query_type"),
                "answer": answer, "notes": case.get("notes"),
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
