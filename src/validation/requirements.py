"""Question-type requirements used by the answer guardrail."""

from __future__ import annotations

from typing import Any, Dict, List

from src.domain.metric_ontology import metric_definition


QUESTION_REQUIREMENTS: Dict[str, List[str]] = {
    "financial_metric": ["company", "period", "metric", "value", "unit", "scope", "citation"],
    "investment_plan": ["company", "period", "annual_plan", "categories", "unit", "citation"],
    "capex_comparison": ["comparison_targets", "values", "compatible_units", "same_period", "citation"],
    "financing_history": ["company", "period", "requested_instruments", "decision_basis", "citation_or_explicit_absence"],
    "contract_termination": ["company", "period", "contract_search", "termination_search", "citation_or_explicit_absence"],
    "business_change": ["company", "comparison_periods", "evidence_for_each_period", "citation"],
    "correction_history": ["company", "correction_before_after_or_limit", "current_effective_or_limit", "citation_or_explicit_absence"],
}


def evaluate_requirements(plan: Dict[str, Any], answer: str, contexts: List[Dict[str, Any]]) -> Dict[str, Any]:
    query_type = plan.get("query_type") or "generic"
    required = QUESTION_REQUIREMENTS.get(query_type, ["citation_or_explicit_limit"])
    citations = [item.get("citation") or {} for item in contexts if item.get("citation")]
    companies = plan.get("companies") or []
    years = plan.get("years") or []
    kinds = {item.get("kind") for item in contexts}
    answer_compact = answer.replace(" ", "").upper()
    metric_tokens = metric_definition(plan.get("metric")).get("aliases", [])
    if not metric_tokens:
        metric_tokens = {
            "operating_margin": ["영업이익률"], "net_margin": ["순이익률"],
            "debt_ratio": ["부채비율"], "roe": ["ROE", "자기자본이익률"],
        }.get(plan.get("metric"), [])
    checks: Dict[str, bool] = {
        "company": bool(companies) and all(company.get("corp_name") in answer for company in companies[:2]),
        "period": not years or all(str(year) in answer for year in years[:2]),
        "metric": bool(plan.get("metric")) and any(
            token.replace(" ", "").upper() in answer_compact
            for token in metric_tokens if token
        ),
        "value": bool(contexts) and any(char.isdigit() for char in answer),
        "unit": any(unit in answer for unit in ("원", "백만원", "억원", "%", "주", "단위")),
        "scope": plan.get("scope") is None or any(scope in answer for scope in ("연결", "별도", "기준 미상")),
        "citation": bool(citations) and all(citation.get("rcept_no") for citation in citations[:1]),
        "annual_plan": "연간계획" in answer_compact or "투자계획" in answer_compact,
        "categories": query_type != "investment_plan" or sum(token in answer_compact for token in ("R&D", "CAPEX", "전략투자", "증설/보완")) >= 2,
        "comparison_targets": len(companies) >= 2 and all(company.get("corp_name") in answer for company in companies[:2]),
        "values": query_type != "capex_comparison" or sum(char.isdigit() for char in answer) >= 2,
        "compatible_units": query_type != "capex_comparison" or ("단위" in answer and not "자동 판단하지 않" in answer),
        "same_period": len(years) == 1 and str(years[0]) in answer if query_type == "capex_comparison" else True,
        "requested_instruments": query_type != "financing_history" or all(
            {"equity": "유상증자", "CB": "CB", "BW": "BW", "EB": "EB"}.get(instrument, instrument) in answer_compact
            for instrument in plan.get("funding_instruments", ["equity", "CB", "BW", "EB"])
        ),
        "decision_basis": query_type != "financing_history" or "결정공시" in answer_compact,
        "contract_search": query_type != "contract_termination" or ("계약" in answer and (bool(contexts) or "찾지 못" in answer)),
        "termination_search": query_type != "contract_termination" or "해지" in answer,
        "comparison_periods": query_type != "business_change" or (
            (plan.get("comparison_axis") == "doc_subtype" and "사업보고서" in answer and "분기보고서" in answer) or
            (len(years) >= 2 and all(str(year) in answer for year in years[:2]))
        ),
        "evidence_for_each_period": query_type != "business_change" or (
            (plan.get("comparison_axis") == "doc_subtype" and all(
                any(label in (item.get("citation", {}).get("report_nm") or "") for item in contexts)
                for label in ("사업보고서", "분기보고서")
            )) or all(
                any(item.get("citation", {}).get("rcept_no") and str(year) in item.get("content", "") for item in contexts)
                for year in years[:2]
            )
        ),
        "correction_before_after_or_limit": query_type != "correction_history" or (
            (plan.get("correction_view") == "current" and "현재 유효" in answer) or
            (plan.get("correction_view") == "original" and "정정 전" in answer) or
            (plan.get("correction_view") not in {"current", "original"} and "정정 전" in answer and "정정 후" in answer) or
            "찾지 못" in answer),
        "current_effective_or_limit": query_type != "correction_history" or
            plan.get("correction_view") == "original" or "현재 유효" in answer or "찾지 못" in answer,
        "citation_or_explicit_absence": bool(citations) or any(token in answer for token in ("없음", "찾지 못", "확인할 수 없")),
        "citation_or_explicit_limit": bool(citations) or any(token in answer for token in ("확인할 수 없", "찾지 못", "공시 코퍼스만으로는", "보안상")),
    }
    missing = [name for name in required if not checks.get(name, False)]
    return {"query_type": query_type, "required": required, "passed": not missing,
            "missing": missing, "checks": {name: checks.get(name, False) for name in required}}
