"""Deterministic multi-intent planning on top of QueryAnalyzer."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from src.domain.metric_ontology import METRICS, METRIC_ONTOLOGY
from .query_analyzer import QueryAnalyzer


METRIC_QUESTION_LABELS = {
    key: definition["label"] for key, definition in METRIC_ONTOLOGY.items()
    if definition.get("source") == "financial_cell"
}


class QueryPlanner:
    def __init__(self, analyzer: QueryAnalyzer):
        self.analyzer = analyzer

    def plan(self, question: str) -> Dict[str, Any]:
        base = self.analyzer.analyze(question)
        subquestions = self._investment_growth_and_direction(question, base)
        if not subquestions:
            subquestions = self._multi_metric_questions(question, base)
        if len(subquestions) < 2:
            return {"is_composite": False, "base_plan": base, "subtasks": []}
        subtasks = []
        for index, subquestion in enumerate(subquestions, start=1):
            subtasks.append({"task_id": f"task_{index:03d}", "question": subquestion,
                             "plan": self.analyzer.analyze(subquestion)})
        return {"is_composite": True, "base_plan": base, "subtasks": subtasks,
                "shared_context": self._shared_context(base),
                "coverage_requirement": "all_subtasks_must_resolve"}

    def _investment_growth_and_direction(self, question: str, base: Dict[str, Any]) -> List[str]:
        if not ("증감률" in question or "증가율" in question or "감소율" in question):
            return []
        if not any(token in question for token in ("투자 방향", "투자전략", "투자 전략")):
            return []
        if base.get("metric") != "capex" or len(base.get("years") or []) < 2 or not base.get("companies"):
            return []
        prefix = self._prefix(base)
        company = base["companies"][0].get("listed_name") or base["companies"][0].get("corp_name")
        years = sorted(base["years"])
        return [
            f"{prefix} 설비투자 증감률은?",
            f"{company}의 {years[0]}년과 {years[-1]}년 사업보고서 주요 투자 방향은 어떻게 변화했나?",
        ]

    def _multi_metric_questions(self, question: str, base: Dict[str, Any]) -> List[str]:
        if base.get("calculation") or base.get("query_type") not in {"financial_metric", "generic"}:
            return []
        metrics = self._mentioned_metrics(question)
        if len(metrics) < 2:
            return []
        prefix = self._prefix(base)
        return [f"{prefix} {METRIC_QUESTION_LABELS[metric]}은?" for metric in metrics]

    @staticmethod
    def _mentioned_metrics(question: str) -> List[str]:
        compact = re.sub(r"\s+", "", question).lower()
        found: List[tuple[int, int, str]] = []
        for metric, aliases in METRICS.items():
            if metric not in METRIC_QUESTION_LABELS:
                continue
            for alias in aliases:
                normalized = re.sub(r"\s+", "", alias).lower()
                start = compact.find(normalized)
                if start >= 0:
                    found.append((start, start + len(normalized), metric))
        # Prefer the most specific alias when one metric name is embedded in
        # another (e.g. 매출 vs 매출총이익), while preserving truly separate mentions.
        chosen: List[tuple[int, int, str]] = []
        for candidate in sorted(found, key=lambda value: (-(value[1] - value[0]), value[0], value[2])):
            if any(candidate[0] < end and start < candidate[1] for start, end, _ in chosen):
                continue
            chosen.append(candidate)
        chosen.sort()
        return list(dict.fromkeys(metric for _, _, metric in chosen))

    @staticmethod
    def _prefix(plan: Dict[str, Any]) -> str:
        parts: List[str] = []
        parts.extend(company.get("listed_name") or company.get("corp_name") for company in plan.get("companies") or [])
        years = plan.get("years") or []
        if len(years) == 1:
            parts.append(f"{years[0]}년")
        elif years:
            parts.append("과 ".join(f"{year}년" for year in years))
        if plan.get("quarter"):
            parts.append(f"{plan['quarter']}분기")
        if plan.get("scope") == "consolidated":
            parts.append("연결")
        elif plan.get("scope") == "separate":
            parts.append("별도")
        subtypes = plan.get("doc_subtypes") or []
        if subtypes == ["annual"]:
            parts.append("사업보고서 기준")
        elif subtypes == ["half"]:
            parts.append("반기보고서 기준")
        elif subtypes == ["quarter"]:
            parts.append("분기보고서 기준")
        return " ".join(part for part in parts if part)

    @staticmethod
    def _shared_context(plan: Dict[str, Any]) -> Dict[str, Any]:
        return {key: plan.get(key) for key in ("companies", "years", "months", "quarter", "scope", "doc_subtypes")}
