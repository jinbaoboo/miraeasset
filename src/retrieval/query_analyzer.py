"""Deterministic query metadata extraction used before optional LLM planning."""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Dict, List, Optional


METRICS = {
    "contract_ratio": ["매출액 대비 비율", "매출액대비"],
    "capex": ["설비투자", "CAPEX", "유형자산 취득"],
    "revenue": ["매출액", "매출", "영업수익"],
    "operating_profit": ["영업이익", "영업손실"],
    "net_income": ["당기순이익", "당기순손실", "분기순이익", "반기순이익"],
    "assets": ["자산총계", "총자산"],
    "liabilities": ["부채총계", "총부채"],
    "equity": ["자본총계", "총자본"],
    "rnd": ["연구개발비", "연구개발비용", "R&D"],
    "contract_amount": ["계약금액"],
    "holding_ratio": ["보유비율", "비율"],
}

CALCULATION_PATTERNS = {
    "growth_rate": ["전년대비", "전년 대비", "전년동기대비", "전년 동기 대비", "증감률", "증가율", "감소율", "성장률", "yoy"],
    "difference": ["차이", "얼마나 증가", "얼마나 감소", "증감액"],
    "sum": ["합계", "총액", "총합"],
    "average": ["평균"],
    "ratio": ["비중", "비율"],
}

DERIVED_CALCULATIONS = {
    "operating_margin": {
        "aliases": ["영업이익률"],
        "operation": "ratio",
        "required_metrics": ["operating_profit", "revenue"],
        "formula": "영업이익 / 매출액 * 100",
    },
    "net_margin": {
        "aliases": ["순이익률", "당기순이익률"],
        "operation": "ratio",
        "required_metrics": ["net_income", "revenue"],
        "formula": "당기순이익 / 매출액 * 100",
    },
    "debt_ratio": {
        "aliases": ["부채비율"],
        "operation": "ratio",
        "required_metrics": ["liabilities", "equity"],
        "formula": "부채총계 / 자본총계 * 100",
    },
    "roe": {
        "aliases": ["ROE", "자기자본이익률"],
        "operation": "ratio",
        "required_metrics": ["net_income", "equity"],
        "formula": "당기순이익 / 자본총계 * 100",
    },
}


class QueryAnalyzer:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def analyze(self, question: str) -> Dict[str, Any]:
        compact_question = re.sub(r"\s+", "", question).lower()
        companies = self._companies(question)
        years = sorted({int(value) for value in re.findall(r"(20\d{2})\s*년?", question)})
        months = [int(value) for value in re.findall(r"(?<!\d)(1[0-2]|[1-9])\s*월", question)]
        quarter_match = re.search(r"([1-4])\s*분기", question)
        quarter = int(quarter_match.group(1)) if quarter_match else None
        doc_subtypes: List[str] = []
        if "사업보고서" in question:
            doc_subtypes.append("annual")
        if "반기보고서" in question or re.search(r"\b반기\b", question):
            doc_subtypes.append("half")
        if "분기보고서" in question or quarter:
            doc_subtypes.append("quarter")
        scope = "consolidated" if "연결" in question else "separate" if any(x in question for x in ("별도", "개별")) else None
        cross_corpus = any(x in question for x in ("가장", "상위", "하위", "전체 기업", "기업 중", "순위"))
        metric = next((key for key, aliases in METRICS.items()
                       if any(re.sub(r"\s+", "", alias).lower() in compact_question for alias in aliases)), None)
        calculation = self._calculation(question, compact_question, metric, years)
        required_metrics = calculation.get("required_metrics", [metric] if metric else []) if calculation else ([metric] if metric else [])
        if calculation and calculation.get("derived_metric"):
            metric = calculation["derived_metric"]
        groups: List[str] = []
        if any(x in question for x in ("사업보고서", "반기보고서", "분기보고서", "재무", "매출", "영업이익", "당기순이익", "연구개발", "설비투자", "CAPEX", "이익률", "부채비율", "ROE", "자기자본이익률")):
            groups.append("periodic")
        if any(x in question for x in ("유상증자", "자기주식", "합병", "분할", "사채", "주요사항", "자금조달",
                                       "CB", "BW", "EB", "전환사채", "신주인수권", "교환사채")):
            groups.append("major")
        if any(x in question for x in ("공급계약", "주요 계약", "계약 해지", "수주", "시설투자", "투자판단", "거래소", "콜옵션", "주주간계약")):
            groups.append("exchange")
        if any(x in question for x in ("대량보유", "지분", "보유비율", "특별관계자")):
            groups.append("holding")
        intent = "comparison" if cross_corpus or any(x in question for x in ("비교", "차이", "증가", "감소", "증감", "대비", "더 큰", "더 많", "더 적")) else "lookup"
        if calculation or any(x in question for x in ("합계", "총액", "평균", "비중", "증감률")):
            intent = "calculation"
        plan = {
            "question": question, "companies": companies, "years": years, "months": months,
            "quarter": quarter, "scope": scope, "metric": metric, "doc_subtypes": list(dict.fromkeys(doc_subtypes)),
            "cross_corpus": cross_corpus,
            "doc_groups": list(dict.fromkeys(groups)), "intent": intent,
            "period_basis": self._period_basis(question, groups),
            "required_metrics": list(dict.fromkeys(required_metrics)),
            "calculation": calculation,
            "requires_current_effective": not any(x in question for x in ("정정 전", "변경 전", "최초")),
        }
        plan["missing_slots"] = self._missing_slots(plan)
        plan["warnings"] = self._warnings(question, plan)
        return plan

    def _companies(self, question: str) -> List[Dict[str, Optional[str]]]:
        rows = self.conn.execute("SELECT corp_code,stock_code,corp_name,listed_name FROM companies ORDER BY length(corp_name) DESC").fetchall()
        found = []
        for row in rows:
            names = {row["corp_name"], row["listed_name"], row["stock_code"]}
            if any(name and name in question for name in names):
                found.append(dict(row))
        return found

    @staticmethod
    def _calculation(question: str, compact_question: str, metric: Optional[str], years: List[int]) -> Optional[Dict[str, Any]]:
        for name, spec in DERIVED_CALCULATIONS.items():
            if any(re.sub(r"\s+", "", alias).lower() in compact_question for alias in spec["aliases"]):
                return {
                    "name": name,
                    "operation": spec["operation"],
                    "required_metrics": spec["required_metrics"],
                    "formula": spec["formula"],
                    "derived_metric": name,
                }
        operation = None
        for candidate, aliases in CALCULATION_PATTERNS.items():
            if any(re.sub(r"\s+", "", alias).lower() in compact_question for alias in aliases):
                operation = candidate
                break
        if operation is None:
            return None
        calculation: Dict[str, Any] = {"operation": operation}
        if operation == "growth_rate":
            calculation["formula"] = "(current - baseline) / abs(baseline) * 100"
            if metric:
                calculation["required_metrics"] = [metric]
            if years:
                target_year = max(years)
                calculation["target_year"] = target_year
                calculation["baseline_year"] = min(years) if len(years) > 1 else target_year - 1
        return calculation

    @staticmethod
    def _period_basis(question: str, groups: List[str]) -> str:
        if any(x in question for x in ("체결", "해지", "공시한", "공시일", "자금조달", "유상증자", "CB", "BW", "EB")):
            return "disclosure_or_event_date"
        if "periodic" in groups or any(x in question for x in ("사업보고서", "반기보고서", "분기보고서", "재무", "매출", "영업이익")):
            return "base_period"
        return "unspecified"

    @staticmethod
    def _missing_slots(plan: Dict[str, Any]) -> List[str]:
        missing: List[str] = []
        if not plan.get("companies") and not plan.get("cross_corpus"):
            missing.append("company")
        if plan.get("intent") == "comparison" and not plan.get("cross_corpus") and len(plan.get("companies") or []) < 2:
            missing.append("comparison_target")
        if plan.get("intent") in {"lookup", "comparison", "calculation"} and not plan.get("metric") and not plan.get("required_metrics"):
            missing.append("metric")
        calculation = plan.get("calculation") or {}
        if calculation.get("operation") == "growth_rate" and not calculation.get("baseline_year"):
            missing.append("baseline_period")
        return list(dict.fromkeys(missing))

    @staticmethod
    def _warnings(question: str, plan: Dict[str, Any]) -> List[str]:
        warnings: List[str] = []
        if "수익률" in question and not any(x in question for x in ("영업이익률", "순이익률", "ROE", "자기자본이익률")):
            warnings.append("ambiguous_return_metric")
        if any(year < 2023 or year > 2026 for year in plan.get("years", [])):
            warnings.append("year_outside_corpus_range")
        return warnings
