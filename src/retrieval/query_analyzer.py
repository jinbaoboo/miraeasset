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
        groups: List[str] = []
        if any(x in question for x in ("사업보고서", "반기보고서", "분기보고서", "재무", "매출", "영업이익", "당기순이익", "연구개발", "설비투자", "CAPEX")):
            groups.append("periodic")
        if any(x in question for x in ("유상증자", "자기주식", "합병", "분할", "사채", "주요사항", "자금조달",
                                       "CB", "BW", "EB", "전환사채", "신주인수권", "교환사채")):
            groups.append("major")
        if any(x in question for x in ("공급계약", "주요 계약", "계약 해지", "수주", "시설투자", "투자판단", "거래소", "콜옵션", "주주간계약")):
            groups.append("exchange")
        if any(x in question for x in ("대량보유", "지분", "보유비율", "특별관계자")):
            groups.append("holding")
        intent = "comparison" if cross_corpus or any(x in question for x in ("비교", "차이", "증가", "감소", "증감", "대비", "더 큰", "더 많", "더 적")) else "lookup"
        if any(x in question for x in ("합계", "총액", "평균", "비중", "증감률")):
            intent = "calculation"
        return {
            "question": question, "companies": companies, "years": years, "months": months,
            "quarter": quarter, "scope": scope, "metric": metric, "doc_subtypes": list(dict.fromkeys(doc_subtypes)),
            "cross_corpus": cross_corpus,
            "doc_groups": list(dict.fromkeys(groups)), "intent": intent,
            "requires_current_effective": not any(x in question for x in ("정정 전", "변경 전", "최초")),
        }

    def _companies(self, question: str) -> List[Dict[str, Optional[str]]]:
        rows = self.conn.execute("SELECT corp_code,stock_code,corp_name,listed_name FROM companies ORDER BY length(corp_name) DESC").fetchall()
        found = []
        for row in rows:
            names = {row["corp_name"], row["listed_name"], row["stock_code"]}
            if any(name and name in question for name in names):
                found.append(dict(row))
        return found
