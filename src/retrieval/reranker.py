"""Auditable deterministic reranking with light result diversification."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Sequence


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[0-9A-Za-z가-힣]+", text or "") if len(token) > 1}


class EvidenceReranker:
    def rerank(self, question: str, candidates: Sequence[Dict[str, Any]], plan: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        query_tokens = _tokens(question)
        rescored: List[Dict[str, Any]] = []
        for original in candidates:
            item = dict(original)
            citation = item.get("citation") or {}
            content_tokens = _tokens(item.get("content") or "")
            coverage = len(query_tokens & content_tokens) / max(1, len(query_tokens))
            section = citation.get("section_path") or item.get("section_path") or ""
            section_match = any(expected in section for expected in (plan.get("section_filters") or []))
            breakdown = {
                "retrieval": round(float(item.get("score") or 0), 6),
                "query_coverage": round(coverage * 6, 6),
                "section_match": 4.0 if section_match else 0.0,
                "source_preference": self._source_bonus(item, plan),
            }
            item["score_breakdown"] = breakdown
            item["score"] = sum(breakdown.values())
            item["_diversity_section"] = (item.get("doc_id"), section, item.get("kind"))
            item["_content_tokens"] = content_tokens
            rescored.append(item)
        rescored.sort(key=lambda item: (-item["score"], item.get("rank", 0), item.get("record_id", "")))
        selected = self._diversify(rescored, limit)
        for item in selected:
            item.pop("_diversity_section", None); item.pop("_content_tokens", None)
        return selected

    @staticmethod
    def _source_bonus(item: Dict[str, Any], plan: Dict[str, Any]) -> float:
        if (plan.get("required_metrics") or plan.get("metric")) and item.get("kind") in {"table", "event"}:
            return 3.0
        if plan.get("query_type") in {"generic", "business_change"} and item.get("kind") == "text":
            return 2.0
        return 0.0

    @staticmethod
    def _diversify(candidates: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        section_counts: Counter = Counter(); doc_counts: Counter = Counter()
        doc_count = len({item.get("doc_id") for item in candidates if item.get("doc_id")})
        max_per_doc = limit if doc_count <= 1 else max(2, (limit + doc_count - 1) // doc_count + 1)

        def near_duplicate(item: Dict[str, Any]) -> bool:
            tokens = item.get("_content_tokens") or set()
            return any(tokens and (prior.get("_content_tokens") or set()) and
                       len(tokens & prior["_content_tokens"]) / max(1, len(tokens | prior["_content_tokens"])) >= 0.9
                       for prior in selected)

        deferred: List[Dict[str, Any]] = []
        for item in candidates:
            key = item["_diversity_section"]; doc_id = item.get("doc_id")
            if section_counts[key] >= 2 or doc_counts[doc_id] >= max_per_doc or near_duplicate(item):
                deferred.append(item); continue
            selected.append(item); section_counts[key] += 1; doc_counts[doc_id] += 1
            if len(selected) >= limit:
                return selected
        for item in deferred:
            if not near_duplicate(item):
                selected.append(item)
            if len(selected) >= limit:
                break
        return selected

