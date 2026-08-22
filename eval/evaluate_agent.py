"""Offline groundedness/retrieval smoke evaluation for the local agent."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from src.agent.disclosure_agent import DisclosureAgent


def load_cases(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate(db: Path, questions: Path, output: Path) -> Dict[str, Any]:
    agent = DisclosureAgent(db); results = []
    try:
        for case in load_cases(questions):
            started = time.perf_counter()
            response = agent.answer(case["question_id"], case["question"], use_llm=False)
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            answer = response["answer"]
            contexts = response["retrieved_context"]
            cited_docs = {item.get("citation", {}).get("doc_id") for item in contexts}
            expected_docs = set(case.get("expected_doc_ids", []))
            retrieval_ok = not expected_docs or bool(cited_docs & expected_docs)
            haystack = answer + "\n" + "\n".join(item.get("content", "") for item in contexts)
            terms_ok = all(term in haystack for term in case.get("expected_terms", []))
            refusal_markers = ("확인할 수 없", "찾지 못", "공시 코퍼스만으로는", "보안상")
            refused = not contexts and any(marker in answer for marker in refusal_markers)
            refusal_ok = refused if case.get("expected_refusal") else True
            citation_ok = refused or bool(contexts and all(item.get("citation") for item in contexts))
            passed = retrieval_ok and terms_ok and refusal_ok and citation_ok
            results.append({"question_id": case["question_id"], "category": case["category"], "passed": passed,
                            "retrieval_ok": retrieval_ok, "terms_ok": terms_ok, "refusal_ok": refusal_ok,
                            "citation_ok": citation_ok, "cited_docs": sorted(doc for doc in cited_docs if doc),
                            "latency_ms": latency_ms, "answer": answer})
    finally:
        agent.close()
    summary = Counter("pass" if row["passed"] else "fail" for row in results)
    by_category: Dict[str, Counter] = {}
    for row in results:
        by_category.setdefault(row["category"], Counter())["pass" if row["passed"] else "fail"] += 1
    latencies = sorted(row["latency_ms"] for row in results)
    percentile_95_index = min(len(latencies) - 1, max(0, math.ceil(len(latencies) * 0.95) - 1)) if latencies else 0
    payload = {"total": len(results), "summary": dict(summary),
               "latency_ms": {"median": round(statistics.median(latencies), 2) if latencies else None,
                              "p95": latencies[percentile_95_index] if latencies else None,
                              "max": max(latencies) if latencies else None},
               "by_category": {key: dict(value) for key, value in by_category.items()}, "results": results}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--questions", type=Path, default=Path("eval/golden_questions.jsonl"))
    ap.add_argument("--output", type=Path, default=Path("eval/results.json")); args = ap.parse_args()
    result = evaluate(args.db, args.questions, args.output)
    print(json.dumps({"total": result["total"], "summary": result["summary"], "latency_ms": result["latency_ms"],
                      "by_category": result["by_category"]}, ensure_ascii=False, indent=2))
    return 1 if result["summary"].get("fail") else 0


if __name__ == "__main__":
    raise SystemExit(main())
