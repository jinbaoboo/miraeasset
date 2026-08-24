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


EXTRA_PARAPHRASES = {
    "q_core_001": ["2025년 현대차 연결 매출액을 사업보고서 기준으로 알려줘"],
    "q_core_002": ["현대차 2026년 1분기 보고서에 나온 연간 투자계획과 1분기 집행실적을 정리해줘"],
}


def load_cases(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def expand_cases(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create deterministic meaning-preserving robustness variants.

    The expected evidence and facts remain inherited from the manually curated
    base case.  This tests wrapper/noise sensitivity without inventing new gold
    labels.  Explicit targeted paraphrases cover high-value routes.
    """
    expanded: List[Dict[str, Any]] = []
    wrappers = (
        ("base", lambda question: question),
        ("evidence_prefix", lambda question: f"공시자료를 근거로 답해줘. {question}"),
        ("citation_suffix", lambda question: f"{question} 답변에는 근거 공시와 접수번호도 표시해줘."),
        ("instruction_wrapper", lambda question: f"다음 질의를 정확히 처리해줘: {question}"),
    )
    for case in cases:
        for variant_type, transform in wrappers:
            variant = dict(case)
            variant["base_question_id"] = case["question_id"]
            variant["question_id"] = f"{case['question_id']}__{variant_type}"
            variant["question"] = transform(case["question"])
            variant["variant_type"] = variant_type
            expanded.append(variant)
        for index, question in enumerate(EXTRA_PARAPHRASES.get(case["question_id"], []), start=1):
            variant = dict(case)
            variant.update({"base_question_id": case["question_id"],
                            "question_id": f"{case['question_id']}__targeted_{index}",
                            "question": question, "variant_type": "targeted_paraphrase"})
            expanded.append(variant)
    return expanded


def evaluate(db: Path, questions: Path, output: Path) -> Dict[str, Any]:
    agent = DisclosureAgent(db); results = []
    try:
        base_cases = load_cases(questions)
        for case in expand_cases(base_cases):
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
            validation = response.get("validation") or {}
            expected_limit = bool(case.get("expected_refusal"))
            requirement_ok = validation.get("action") in {"allow", "limit"} if not expected_limit else refused
            groundedness_ok = (validation.get("grounding") or {}).get("passed", True)
            claims_ok = all(claim.get("verified") for claim in response.get("claims", []))
            checks = {"accuracy": terms_ok, "evidence_retrieval": retrieval_ok,
                      "requirement_satisfaction": requirement_ok, "groundedness": groundedness_ok and claims_ok,
                      "citation": citation_ok, "safe_refusal": refusal_ok}
            passed = all(checks.values())
            results.append({"question_id": case["question_id"], "base_question_id": case["base_question_id"],
                            "variant_type": case["variant_type"], "category": case["category"], "passed": passed,
                            "retrieval_ok": retrieval_ok, "terms_ok": terms_ok, "refusal_ok": refusal_ok,
                            "citation_ok": citation_ok, "cited_docs": sorted(doc for doc in cited_docs if doc),
                            "checks": checks, "validation_action": validation.get("action"),
                            "latency_ms": latency_ms, "answer": answer})
    finally:
        agent.close()
    summary = Counter("pass" if row["passed"] else "fail" for row in results)
    by_category: Dict[str, Counter] = {}
    for row in results:
        by_category.setdefault(row["category"], Counter())["pass" if row["passed"] else "fail"] += 1
    latencies = sorted(row["latency_ms"] for row in results)
    percentile_95_index = min(len(latencies) - 1, max(0, math.ceil(len(latencies) * 0.95) - 1)) if latencies else 0
    by_variant: Dict[str, Counter] = {}
    for row in results:
        by_variant.setdefault(row["variant_type"], Counter())["pass" if row["passed"] else "fail"] += 1
    payload = {"base_total": len(load_cases(questions)), "total": len(results), "summary": dict(summary),
               "latency_ms": {"median": round(statistics.median(latencies), 2) if latencies else None,
                              "p95": latencies[percentile_95_index] if latencies else None,
                              "max": max(latencies) if latencies else None},
               "by_category": {key: dict(value) for key, value in by_category.items()},
               "by_variant": {key: dict(value) for key, value in by_variant.items()}, "results": results}
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
