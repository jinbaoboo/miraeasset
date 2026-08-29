"""Canonical financial and disclosure metric definitions."""

from __future__ import annotations

from typing import Any, Dict, Optional


METRIC_ONTOLOGY: Dict[str, Dict[str, Any]] = {
    "revenue": {"label": "매출액", "aliases": ["매출액", "매출", "영업수익"], "source": "financial_cell", "statement_types": ["income_statement", "comprehensive_income_statement"], "sign_policy": "reported"},
    "operating_profit": {"label": "영업이익", "aliases": ["영업이익", "영업손실"], "source": "financial_cell", "statement_types": ["income_statement", "comprehensive_income_statement"], "sign_policy": "reported"},
    "net_income": {"label": "당기순이익", "aliases": ["당기순이익", "순이익", "당기순손실", "분기순이익", "반기순이익"], "source": "financial_cell", "statement_types": ["income_statement", "comprehensive_income_statement"], "sign_policy": "reported"},
    "gross_profit": {"label": "매출총이익", "aliases": ["매출총이익", "매출총손실"], "source": "financial_cell", "statement_types": ["income_statement", "comprehensive_income_statement"], "sign_policy": "reported"},
    "assets": {"label": "자산총계", "aliases": ["자산총계", "총자산"], "source": "financial_cell", "statement_types": ["balance_sheet"], "sign_policy": "reported"},
    "liabilities": {"label": "부채총계", "aliases": ["부채총계", "총부채"], "source": "financial_cell", "statement_types": ["balance_sheet"], "sign_policy": "reported"},
    "equity": {"label": "자본총계", "aliases": ["자본총계", "총자본"], "source": "financial_cell", "statement_types": ["balance_sheet"], "sign_policy": "reported"},
    "cash_and_equivalents": {"label": "현금및현금성자산", "aliases": ["현금및현금성자산", "현금 및 현금성자산"], "source": "financial_cell", "statement_types": ["balance_sheet", "cash_flow_statement"], "sign_policy": "reported"},
    "inventory": {"label": "재고자산", "aliases": ["재고자산"], "source": "financial_cell", "statement_types": ["balance_sheet"], "sign_policy": "reported"},
    "tangible_assets": {"label": "유형자산", "aliases": ["유형자산"], "source": "financial_cell", "statement_types": ["balance_sheet"], "sign_policy": "reported"},
    "intangible_assets": {"label": "무형자산", "aliases": ["무형자산"], "source": "financial_cell", "statement_types": ["balance_sheet"], "sign_policy": "reported"},
    "borrowings": {"label": "차입금", "aliases": ["차입금", "단기차입금", "장기차입금"], "source": "financial_cell", "statement_types": ["balance_sheet"], "sign_policy": "reported"},
    "operating_cash_flow": {"label": "영업활동 현금흐름", "aliases": ["영업활동으로 인한 현금흐름", "영업활동현금흐름"], "source": "financial_cell", "statement_types": ["cash_flow_statement"], "sign_policy": "reported"},
    "investing_cash_flow": {"label": "투자활동 현금흐름", "aliases": ["투자활동으로 인한 현금흐름", "투자활동현금흐름"], "source": "financial_cell", "statement_types": ["cash_flow_statement"], "sign_policy": "reported"},
    "financing_cash_flow": {"label": "재무활동 현금흐름", "aliases": ["재무활동으로 인한 현금흐름", "재무활동현금흐름"], "source": "financial_cell", "statement_types": ["cash_flow_statement"], "sign_policy": "reported"},
    "eps": {"label": "주당순이익", "aliases": ["기본주당이익", "희석주당이익", "주당순이익"], "source": "financial_cell", "statement_types": ["income_statement"], "sign_policy": "reported"},
    "capex": {"label": "설비투자", "aliases": ["설비투자", "CAPEX", "유형자산의 취득", "유형자산 취득"], "source": "financial_cell", "statement_types": ["cash_flow_statement"], "sign_policy": "absolute_cash_outflow_for_size_comparison"},
    "rnd": {"label": "연구개발비", "aliases": ["연구개발비", "연구개발비용", "R&D"], "source": "financial_cell", "statement_types": [], "sign_policy": "reported"},
    "contract_amount": {"label": "계약금액", "aliases": ["계약금액"], "source": "event_field", "statement_types": [], "sign_policy": "reported"},
    "contract_ratio": {"label": "매출액 대비 계약금액 비율", "aliases": ["매출액 대비 비율", "매출액대비"], "source": "event_field", "statement_types": [], "sign_policy": "reported"},
    "holding_ratio": {"label": "보유비율", "aliases": ["보유비율"], "source": "event_field", "statement_types": [], "sign_policy": "reported"},
}

METRICS = {key: definition["aliases"] for key, definition in METRIC_ONTOLOGY.items()}
FINANCIAL_CELL_METRICS = {key for key, definition in METRIC_ONTOLOGY.items() if definition["source"] == "financial_cell"}


def metric_definition(metric: Optional[str]) -> Dict[str, Any]:
    return dict(METRIC_ONTOLOGY.get(metric or "", {}))


def public_metric_definitions() -> Dict[str, Dict[str, Any]]:
    return {
        key: {"label": value["label"], "aliases": list(value["aliases"]), "source": value["source"],
              "statement_types": list(value.get("statement_types", [])), "sign_policy": value["sign_policy"]}
        for key, value in METRIC_ONTOLOGY.items()
    }
