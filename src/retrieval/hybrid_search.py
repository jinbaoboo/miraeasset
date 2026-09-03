"""Metadata-filtered FTS retrieval and exact structured-cell lookup."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.domain.metric_ontology import FINANCIAL_CELL_METRICS, METRICS, metric_definition
from .query_analyzer import COMPANY_ALIASES, COMPANY_NAME_ALIASES
from .reranker import EvidenceReranker

BUSINESS_EVIDENCE_CATEGORIES = {
    "overview": ("사업의 개요", "사업 개요", "영업의 개황"),
    "products": ("주요 제품", "제품 및 서비스", "상품 및 서비스"),
    "segments_revenue": ("사업부문", "부문별 매출", "매출 및 수주", "매출실적", "매출 비중"),
    "new_business": ("신규사업", "신사업", "사업목적 추가", "신규 사업"),
    "strategy_technology": ("중장기 전략", "성장 전략", "전략", "기술", "소프트웨어", "SDV"),
    "rnd": ("연구개발", "R&D", "연구 개발"),
    "investment": ("투자 계획", "투자 현황", "설비의 신설", "CAPEX"),
    "market_change": ("시장 여건", "시장 변화", "산업의 특성", "영업의 개황"),
}

BUSINESS_SIGNALS = {
    "전동화·전기차": ("전동차", "전기차", "EV ", "EV라인업", "IONIQ", "아이오닉"),
    "하이브리드": ("하이브리드", "HEV"),
    "수소": ("수소", "넥쏘"),
    "SDV": ("SDV", "Software Defined Vehicle", "소프트웨어 정의 차량"),
    "자율주행": ("자율주행", "Motional", "Waymo"),
    "PBV": ("PBV",),
    "AAM": ("AAM", "도심항공"),
    "로보틱스": ("로보틱스", "로봇"),
    "AI": ("AI ", "인공지능"),
    "배터리": ("배터리",),
    "현지생산·현지화": ("현지생산", "현지 생산", "현지화", "HMGMA"),
    "전략적 파트너십": ("전략적 협업", "협업", "파트너십", "공동 개발", "협력"),
    "제조혁신": ("Manufacturing Excellence", "제조 혁신", "스마트 팩토리"),
    "제네시스": ("제네시스", "Genesis"),
    "자동차 금융": ("현대캐피탈", "자동차 금융", "금융부문"),
}

AUTOMOTIVE_ONLY_SIGNALS = {
    "전동화·전기차", "하이브리드", "수소", "SDV", "자율주행", "PBV", "AAM",
    "배터리", "현지생산·현지화", "제조혁신", "제네시스", "자동차 금융",
}

AUTOMOTIVE_ONLY_TOPICS = {"완성차·모빌리티"}
TELECOM_ONLY_TOPICS = {"유무선 통신·ICT", "모바일·스마트홈·기업인프라"}

BUSINESS_TOPICS = {
    "서치플랫폼": ("서치플랫폼", "검색 포털", "검색플랫폼"),
    "커머스": ("커머스", "쇼핑"),
    "핀테크": ("핀테크", "네이버페이"),
    "콘텐츠": ("콘텐츠", "웹툰", "웹소설"),
    "엔터프라이즈": ("엔터프라이즈", "기업용 솔루션"),
    "유무선 통신·ICT": ("유무선 통신", "무선통신", "초고속인터넷", "ICT"),
    "금융": ("금융사업", "신용카드", "카드사업"),
    "위성방송": ("위성방송",),
    "부동산": ("부동산사업", "부동산 사업"),
    "게임": ("게임 개발", "모바일게임", "온라인게임", "게임콘텐츠"),
    "메모리 반도체": ("메모리 반도체", "DRAM", "NAND"),
    "파운드리": ("Foundry", "파운드리"),
    "완성차·모빌리티": ("완성차", "차량부문", "모빌리티"),
    "음악·영상 콘텐츠": ("음반", "음원", "영상 컨텐츠", "영상 콘텐츠"),
    "아티스트 매니지먼트": ("매니지먼트", "아티스트"),
    "플랫폼": ("플랫폼 부문", "톡비즈", "포털비즈"),
    "DX": ("DX(Device eXperience)",),
    "DS": ("DS(Device Solutions)",),
    "SDC·OLED": ("SDC가", "OLED 패널"),
    "Harman·전장": ("Harman", "전장제품", "디지털 콕핏"),
    "톡비즈": ("톡비즈",),
    "카카오페이": ("카카오페이",),
    "뮤직": ("뮤직 콘텐츠", "음악플랫폼 멜론"),
    "스토리": ("스토리콘텐츠", "스토리IP"),
    "음반·음원": ("음반, 음원", "음반ㆍ음원", "음반 및 음원"),
    "해운·물류": ("종합해운물류", "컨테이너선", "벌크화물"),
    "CDMO": ("CDMO", "위탁개발생산"),
    "바이오의약품": ("바이오의약품",),
    "바이오시밀러·신약": ("바이오시밀러", "항체의약품", "신규 모달리티"),
    "식품": ("식품사업", "식품 사업", "식품, BIO"),
    "BIO·F&C": ("BIO 및 F&C", "BIO사업", "F&C 사업"),
    "종합물류": ("종합물류서비스", "계약물류", "포워딩"),
    "모바일·스마트홈·기업인프라": ("모바일ㆍ스마트홈ㆍ기업인프라", "모바일·스마트홈·기업인프라"),
    "철강": ("철강부문", "철강 부문"),
    "인프라": ("인프라부문", "인프라 부문"),
    "에너지소재": ("에너지소재부문", "에너지소재 부문", "이차전지소재"),
    "광학솔루션": ("광학솔루션",),
    "기판소재": ("기판소재", "기판/소재"),
    "전장부품": ("전장부품",),
    "전력기기": ("전력기기", "변압기", "고압차단기", "회전기기", "배전기기"),
    "건설": ("건축/주택", "토목사업", "플랜트사업", "건설사업"),
}


def _fts_query(text: str) -> str:
    tokens = [token for token in re.findall(r"[0-9A-Za-z가-힣]+", text) if len(token) > 1]
    expansions = []
    compact = re.sub(r"[^0-9A-Za-z가-힣]", "", text)
    if "투자판단" in compact and "주요경영사항" in compact:
        expansions.append("투자판단관련주요경영사항")
    if "신규시설투자" in compact:
        expansions.append("신규시설투자등")
    if "단일판매" in compact or "공급계약" in compact:
        expansions.append("단일판매공급계약체결")
    if any(token in compact for token in ("주요제품", "제품과서비스", "주요서비스")):
        expansions.extend(("주요 제품", "제품", "서비스", "사업의 개요"))
    if any(token in compact for token in ("주요사업", "사업내용", "사업의내용")):
        expansions.extend(("사업의 개요", "주요 제품", "매출 및 수주"))
    tokens = expansions + tokens
    return " OR ".join('"' + token.replace('"', '') + '"' for token in tokens[:20]) or '"공시"'


class HybridRetriever:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        self.reranker = EvidenceReranker()

    def candidate_documents(self, plan: Dict[str, Any], limit: int = 2000) -> List[Dict[str, Any]]:
        where: List[str] = []
        args: List[Any] = []
        companies = plan.get("companies") or []
        if companies:
            where.append("corp_code IN (" + ",".join("?" for _ in companies) + ")")
            args.extend(company["corp_code"] for company in companies)
        if plan.get("doc_groups"):
            where.append("doc_group IN (" + ",".join("?" for _ in plan["doc_groups"]) + ")")
            args.extend(plan["doc_groups"])
        if plan.get("doc_subtypes"):
            where.append("doc_subtype IN (" + ",".join("?" for _ in plan["doc_subtypes"]) + ")")
            args.extend(plan["doc_subtypes"])
        years = self._candidate_years(plan)
        if years:
            where.append("(base_year IN (" + ",".join("?" for _ in years) + ") OR "
                         "(base_year IS NULL AND substr(rcept_dt,1,4) IN (" +
                         ",".join("?" for _ in years) + ")))")
            args.extend(years)
            args.extend(str(year) for year in years)
        if plan.get("months"):
            months = plan["months"]
            where.append("(base_month IN (" + ",".join("?" for _ in months) + ") OR "
                         "(base_month IS NULL AND cast(substr(rcept_dt,5,2) AS INTEGER) IN (" +
                         ",".join("?" for _ in months) + ")))")
            args.extend(months)
            args.extend(months)
        if plan.get("quarter"):
            where.append("(base_month=? OR (base_month IS NULL AND cast(substr(rcept_dt,5,2) AS INTEGER) BETWEEN ? AND ?))")
            quarter_month = plan["quarter"] * 3
            args.extend([quarter_month, quarter_month - 2, quarter_month])
        if plan.get("requires_current_effective", True):
            where.append("is_latest_version=1")
        if not where:
            return []
        args.append(limit)
        rows = self.conn.execute(
            f"""SELECT doc_id,corp_code,corp_name,report_nm,rcept_no,rcept_dt,doc_group,doc_subtype,
                       base_year,base_month,is_latest_version
                FROM documents WHERE {' AND '.join(where)}
                ORDER BY corp_name,doc_group,base_year DESC,base_month DESC,rcept_dt DESC LIMIT ?""",
            args,
        ).fetchall()
        return [dict(row) for row in rows]

    def search(self, question: str, plan: Dict[str, Any], limit: int = 8) -> List[Dict[str, Any]]:
        query = _fts_query(question)
        candidates: List[Dict[str, Any]] = []
        for kind, fts, id_col, body_col in (
            ("text", "chunks_fts", "chunk_id", "text"),
            ("table", "tables_fts", "table_id", "search_text"),
            ("event", "events_fts", "event_id", "search_text"),
            ):
            where = [f"{fts} MATCH ?"]
            args: List[Any] = [query]
            candidate_doc_ids = plan.get("_candidate_doc_ids") or []
            if candidate_doc_ids:
                where.append("d.doc_id IN (" + ",".join("?" for _ in candidate_doc_ids) + ")")
                args.extend(candidate_doc_ids)
            companies = plan.get("companies") or []
            if companies:
                where.append("d.corp_code IN (" + ",".join("?" for _ in companies) + ")")
                args.extend(company["corp_code"] for company in companies)
            if plan.get("doc_groups"):
                where.append("d.doc_group IN (" + ",".join("?" for _ in plan["doc_groups"]) + ")")
                args.extend(plan["doc_groups"])
            if plan.get("doc_subtypes"):
                where.append("d.doc_subtype IN (" + ",".join("?" for _ in plan["doc_subtypes"]) + ")")
                args.extend(plan["doc_subtypes"])
            years = self._candidate_years(plan)
            if years:
                where.append("(d.base_year IN (" + ",".join("?" for _ in years) + ") OR "
                             "(d.base_year IS NULL AND substr(d.rcept_dt,1,4) IN (" +
                             ",".join("?" for _ in years) + ")))")
                args.extend(years); args.extend(str(year) for year in years)
            if plan.get("months"):
                where.append("(d.base_month IN (" + ",".join("?" for _ in plan["months"]) + ") OR "
                             "(d.base_month IS NULL AND cast(substr(d.rcept_dt,5,2) AS INTEGER) IN (" +
                             ",".join("?" for _ in plan["months"]) + ")))")
                args.extend(plan["months"]); args.extend(plan["months"])
            if plan.get("requires_current_effective", True):
                where.append("d.is_latest_version=1")
            section_filters = plan.get("section_filters") or []
            if section_filters and kind in {"text", "table"}:
                where.append("(" + " OR ".join("f.section_path LIKE ?" for _ in section_filters) + ")")
                args.extend(f"%{section}%" for section in section_filters)
            args.append(limit * 4)
            try:
                rows = self.conn.execute(
                    f"""SELECT f.{id_col} AS record_id,f.doc_id,bm25({fts}) AS rank,
                               f.corp_name,f.report_nm,f.{body_col} AS content,d.rcept_no,d.rcept_dt,
                               d.doc_group,d.doc_subtype,d.base_year,d.base_month,d.is_latest_version
                        FROM {fts} f JOIN documents d ON d.doc_id=f.doc_id
                        WHERE {' AND '.join(where)} ORDER BY rank LIMIT ?""",
                    args,
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            for row in rows:
                item = dict(row); item["kind"] = kind
                if not self._allowed(item, plan):
                    continue
                item["score"] = self._score(item, plan)
                item["citation"] = self.citation_for(kind, item["record_id"])
                candidates.append(item)
        candidates.sort(key=lambda item: (-item["score"], item.get("rank", 0)))
        seen = set(); unique = []
        for item in candidates:
            key = (item["kind"], item["record_id"])
            if key not in seen:
                seen.add(key); unique.append(item)
        return self.reranker.rerank(question, unique, plan, limit)

    def find_metric_cells(self, plan: Dict[str, Any], limit: int = 30) -> List[Dict[str, Any]]:
        return self.find_metric_cells_for_metric(plan, plan.get("metric"), limit=limit)

    def find_metric_cells_for_metric(self, plan: Dict[str, Any], metric: Optional[str], limit: int = 30) -> List[Dict[str, Any]]:
        aliases = METRICS.get(metric or "", [])
        if not aliases:
            return []
        where = ["c.numeric_value IS NOT NULL", "d.doc_group='periodic'"]
        args: List[Any] = []
        candidate_doc_ids = plan.get("_candidate_doc_ids") or []
        if candidate_doc_ids:
            where.append("d.doc_id IN (" + ",".join("?" for _ in candidate_doc_ids) + ")")
            args.extend(candidate_doc_ids)
        where.append("(" + " OR ".join("c.row_label LIKE ?" for _ in aliases) + ")")
        args.extend("%" + alias + "%" for alias in aliases)
        companies = plan.get("companies") or []
        if companies:
            where.append("d.corp_code IN (" + ",".join("?" for _ in companies) + ")")
            args.extend(company["corp_code"] for company in companies)
        years = self._candidate_years(plan)
        if years:
            where.append("d.base_year IN (" + ",".join("?" for _ in years) + ")")
            args.extend(years)
        if plan.get("doc_subtypes"):
            where.append("d.doc_subtype IN (" + ",".join("?" for _ in plan["doc_subtypes"]) + ")")
            args.extend(plan["doc_subtypes"])
        if plan.get("quarter"):
            where.append("d.base_month=?"); args.append(plan["quarter"] * 3)
        if plan.get("scope"):
            where.append("c.scope=?"); args.append(plan["scope"])
        if plan.get("requires_current_effective", True):
            where.append("d.is_latest_version=1")
        sql = f"""SELECT c.*,t.table_title,t.section_path,t.statement_type,t.unit_json,d.corp_code,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,
                          d.base_year,d.base_month,d.doc_id,d.doc_subtype,d.is_latest_version
                   FROM cells c JOIN logical_tables t ON t.table_id=c.table_id
                   JOIN documents d ON d.doc_id=c.doc_id WHERE {' AND '.join(where)}
                   ORDER BY d.base_year DESC,d.base_month DESC,d.rcept_dt DESC LIMIT ?"""
        # Ranking happens after row retrieval because it depends on normalized
        # labels, statement type and period role.  A small LIMIT here can cut
        # off the primary statement before scoring (notes often appear first).
        args.append(max(500, limit * 10))
        rows = [dict(row) for row in self.conn.execute(sql, args).fetchall()]
        for row in rows:
            if row.get("unit_currency") is None or row.get("unit_scale") is None:
                recovered_unit = self._effective_table_unit(row)
                if recovered_unit.get("currency") is not None and recovered_unit.get("scale") is not None:
                    row.update({"unit_raw": recovered_unit.get("raw"),
                                "unit_currency": recovered_unit.get("currency"),
                                "unit_scale": recovered_unit.get("scale")})
            row["metric"] = metric
            row["selection_score"] = self._cell_score(row, plan, metric)
            row["citation"] = self.citation_for("cell", row["cell_id"])
        rows.sort(key=lambda row: -row["selection_score"])
        # Preserve at least one candidate for every requested comparison year;
        # otherwise equal-scored current-year note rows can exhaust the limit
        # before the baseline year is seen.
        requested_years = self._candidate_years(plan)
        balanced: List[Dict[str, Any]] = []
        chosen = set()
        for year in requested_years:
            candidate = next((row for row in rows if row.get("base_year") == year), None)
            if candidate:
                balanced.append(candidate); chosen.add(candidate.get("cell_id"))
        balanced.extend(row for row in rows if row.get("cell_id") not in chosen)
        return balanced[:limit]

    def find_dimension_metric_cells(self, plan: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
        """Resolve segment/site metrics only when the table explicitly links both labels.

        Segment tables often put ``차량부문`` in the row label and ``매출액`` in
        a neighbouring non-numeric cell.  Treating a consolidated statement
        total as the segment value is unsafe, so this route requires the
        dimension and metric marker in the same physical row.
        """
        dimensions = plan.get("dimensions") or []
        metric = plan.get("metric")
        aliases = METRICS.get(metric or "", [])
        candidate_doc_ids = plan.get("_candidate_doc_ids") or []
        if not dimensions or not aliases or not candidate_doc_ids:
            return []
        where = ["c.numeric_value IS NOT NULL",
                 "c.doc_id IN (" + ",".join("?" for _ in candidate_doc_ids) + ")",
                 "(" + " OR ".join("(c.row_label LIKE ? OR c.row_path LIKE ?)" for _ in dimensions) + ")"]
        args: List[Any] = list(candidate_doc_ids)
        for dimension in dimensions:
            args.extend((f"%{dimension}%", f"%{dimension}%"))
        rows = self.conn.execute(
            f"""SELECT c.*,t.table_title,t.section_path,t.statement_type,t.unit_json,
                       d.corp_code,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,d.base_year,d.base_month,
                       d.doc_id,d.doc_subtype,d.is_latest_version
                  FROM cells c JOIN logical_tables t ON t.table_id=c.table_id
                  JOIN documents d ON d.doc_id=c.doc_id
                 WHERE {' AND '.join(where)}
                 ORDER BY d.base_year DESC,d.base_month DESC,c.table_id,c.row_index,c.column_index""", args
        ).fetchall()
        matched: List[Dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            markers = self.conn.execute(
                """SELECT column_index,original_text FROM cells
                    WHERE table_id=? AND row_index=? AND numeric_value IS NULL ORDER BY column_index""",
                (row["table_id"], row["row_index"]),
            ).fetchall()
            marker = next((item for item in markers if any(
                re.sub(r"\s+", "", alias).lower() in re.sub(r"\s+", "", item["original_text"] or "").lower()
                for alias in aliases)), None)
            if not marker or int(row.get("column_index") or 0) <= int(marker["column_index"]):
                continue
            row["metric"] = metric
            row["row_label"] = " ".join([*dimensions, metric_definition(metric).get("label", marker["original_text"])])
            # The first numeric cell after the metric marker is the current
            # period amount; following cells may be ratios or prior periods.
            distance = int(row["column_index"]) - int(marker["column_index"])
            row["selection_score"] = 1000 - distance * 20 + (10 if row.get("is_latest_version") else 0)
            row["citation"] = self.citation_for("cell", row["cell_id"])
            matched.append(row)
        best_by_row: Dict[tuple, Dict[str, Any]] = {}
        for row in matched:
            key = (row["table_id"], row["row_index"], row.get("metric"))
            if key not in best_by_row or row["selection_score"] > best_by_row[key]["selection_score"]:
                best_by_row[key] = row
        return sorted(best_by_row.values(), key=lambda item: -item["selection_score"])[:limit]

    def find_investment_plan(self, plan: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
        """Return complete investment-plan rows instead of an FTS table excerpt."""
        candidate_doc_ids = plan.get("_candidate_doc_ids") or []
        if not candidate_doc_ids:
            return []
        placeholders = ",".join("?" for _ in candidate_doc_ids)
        tables = self.conn.execute(
            f"""SELECT t.*,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,d.base_year,d.base_month
                  FROM logical_tables t JOIN documents d ON d.doc_id=t.doc_id
                 WHERE t.doc_id IN ({placeholders})
                   AND (t.section_path LIKE '%사업의 내용%')
                   AND (t.search_text LIKE '%투자계획%' OR t.search_text LIKE '%투자 계획%'
                        OR t.search_text LIKE '%주요 투자 현황%' OR t.table_title LIKE '%주요 투자%')""",
            candidate_doc_ids,
        ).fetchall()
        ranked: List[tuple[int, Dict[str, Any]]] = []
        for raw_table in tables:
            table = dict(raw_table)
            cell_rows = self.conn.execute(
                "SELECT * FROM cells WHERE table_id=? ORDER BY row_index,column_index", (table["table_id"],)
            ).fetchall()
            cells = [dict(row) for row in cell_rows]
            numeric = [cell for cell in cells if cell.get("numeric_value") is not None]
            text = (table.get("table_title") or "") + " " + (table.get("search_text") or "")
            score = len(numeric) + (30 if "투자계획" in text.replace(" ", "") else 0)
            score += 20 if any("R&D" in (cell.get("row_label") or "") or "CAPEX" in (cell.get("row_label") or "") for cell in cells) else 0
            if not numeric:
                continue
            unit = self._effective_table_unit(table)
            for cell in cells:
                if not cell.get("unit_raw") and unit.get("raw"):
                    cell.update({"unit_raw": unit.get("raw"), "unit_currency": unit.get("currency"),
                                 "unit_scale": unit.get("scale")})
            headers = {int(cell.get("column_index") or 0): cell.get("original_text") or ""
                       for cell in cells if int(cell.get("row_index") or 0) == 0}
            row_items = {int(cell.get("row_index") or 0): cell.get("original_text") or ""
                         for cell in cells if int(cell.get("row_index") or 0) > 0
                         and int(cell.get("column_index") or 0) == 1 and cell.get("numeric_value") is None}
            grouped: Dict[int, Dict[str, Any]] = {}
            for cell in cells:
                if cell.get("numeric_value") is None:
                    continue
                row_index = int(cell.get("row_index") or 0)
                segment = cell.get("row_label") or ""
                item_label = row_items.get(row_index) or segment
                full_label = " > ".join(dict.fromkeys(part for part in (segment, item_label) if part))
                row = grouped.setdefault(row_index, {"row_index": row_index,
                    "row_label": full_label, "segment": segment, "item": item_label, "values": []})
                row["values"].append({
                    "column_index": cell.get("column_index"),
                    "column_label": headers.get(int(cell.get("column_index") or 0)) or cell.get("column_label") or self._last_json_value(cell.get("column_path")),
                    "column_path": self._json_list(cell.get("column_path")),
                    "original_text": cell.get("original_text"), "numeric_value": cell.get("numeric_value"),
                    "period_label": cell.get("period_label"), "period_role": cell.get("period_role"),
                    "cell_id": cell.get("cell_id"),
                })
            citation = self.citation_for("table", table["table_id"])
            ranked.append((score, {
                "kind": "investment_plan", "record_id": table["table_id"], "table_id": table["table_id"],
                "doc_id": table["doc_id"], "corp_name": table["corp_name"], "report_nm": table["report_nm"],
                "rcept_no": table["rcept_no"], "rcept_dt": table["rcept_dt"], "base_year": table["base_year"],
                "base_month": table["base_month"], "table_title": table.get("table_title"),
                "section_path": table.get("section_path"), "scope": table.get("scope") or "unknown",
                "unit": unit, "rows": list(grouped.values()), "citation": citation,
            }))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in ranked[:limit]]

    def find_financing_events(self, plan: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        """Compatibility view containing the current decision in each lifecycle."""
        lifecycle = self.find_financing_lifecycle(plan, limit=limit)
        return [chain["current"] for chain in lifecycle["chains"]]

    def find_financing_lifecycle(self, plan: Dict[str, Any], limit: int = 100) -> Dict[str, Any]:
        """Link financing decision versions without treating a scheduled payment as completion.

        The distributed corpus contains decision filings and their corrections, but
        does not contain issuance-result/payment-completion filing types.  The
        returned coverage metadata makes that boundary machine readable.
        """
        instrument_types = {
            "equity": ["주요사항보고서(유상증자결정)", "유상증자결정"],
            "CB": ["주요사항보고서(전환사채권발행결정)"],
            "BW": ["주요사항보고서(신주인수권부사채권발행결정)"],
            "EB": ["주요사항보고서(교환사채권발행결정)"],
        }
        selected = plan.get("funding_instruments") or list(instrument_types)
        type_to_instrument = {event_type: instrument for instrument in selected for event_type in instrument_types.get(instrument, [])}
        if not type_to_instrument:
            return {"chains": [], "coverage": self._financing_coverage(False)}
        where = ["e.event_type IN (" + ",".join("?" for _ in type_to_instrument) + ")"]
        args: List[Any] = list(type_to_instrument)
        companies = plan.get("companies") or []
        if companies:
            where.append("d.corp_code IN (" + ",".join("?" for _ in companies) + ")")
            args.extend(company["corp_code"] for company in companies)
        rows = self.conn.execute(
            f"""SELECT e.event_id,e.event_type,e.event_title,e.effective_status,d.doc_id,d.corp_code,d.corp_name,
                       d.report_nm,d.rcept_no,d.rcept_dt,d.is_correction,d.original_doc_id,d.supersedes_doc_id,
                       d.superseded_by_doc_id,d.is_latest_version
                  FROM events e JOIN documents d ON d.doc_id=e.doc_id
                 WHERE {' AND '.join(where)} ORDER BY d.rcept_dt DESC LIMIT ?""", args + [limit * 4]
        ).fetchall()
        result: List[Dict[str, Any]] = []
        for raw in rows:
            event = dict(raw)
            fields = self._event_fields(event["event_id"])
            instrument = type_to_instrument[event["event_type"]]
            amount = self._funding_amount(instrument, fields)
            purposes = self._funding_purposes(fields)
            decision_date = self._field_value(fields, ("이사회결의일", "결정일"))
            payment_date = self._field_value(fields, ("납입일", "납입기일"))
            event.update({"kind": "financing", "record_id": event["event_id"], "instrument": instrument,
                          "amount_krw": amount, "purposes": purposes, "decision_date": decision_date,
                          "scheduled_payment_date": payment_date,
                          "stage": "correction" if event.get("is_correction") else "decision",
                          "fields": fields, "citation": self.citation_for("event", event["event_id"])})
            result.append(event)

        grouped: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
        for event in result:
            root = event.get("original_doc_id") or self._date_key(event.get("decision_date")) or event.get("doc_id")
            grouped[(event.get("corp_code"), event["instrument"], root)].append(event)

        years = {str(year) for year in plan.get("years") or []}
        chains: List[Dict[str, Any]] = []
        for (corp_code, instrument, root), versions in grouped.items():
            versions.sort(key=lambda item: (item.get("rcept_dt") or "", item.get("rcept_no") or ""))
            current = versions[-1]
            decision_year = self._date_key(current.get("decision_date"))[:4] or (versions[0].get("rcept_dt") or "")[:4]
            if years and decision_year not in years:
                continue
            lifecycle_id = f"funding:{corp_code}:{instrument}:{root}"
            for version in versions:
                version["lifecycle_id"] = lifecycle_id
                version["lifecycle_status"] = "decision_only"
                version["completed_amount_krw"] = None
            chains.append({
                "lifecycle_id": lifecycle_id,
                "corp_code": corp_code,
                "corp_name": current.get("corp_name"),
                "instrument": instrument,
                "decision_date": current.get("decision_date"),
                "scheduled_payment_date": current.get("scheduled_payment_date"),
                "planned_amount_krw": current.get("amount_krw"),
                "completed_amount_krw": None,
                "status": "decision_only",
                "current": current,
                "history": versions,
                "limitations": ["completion_evidence_not_in_corpus"],
            })
        chains.sort(key=lambda chain: (chain["current"].get("rcept_dt") or "", chain["lifecycle_id"]), reverse=True)
        return {"chains": chains[:limit], "coverage": self._financing_coverage(any(len(chain["history"]) > 1 for chain in chains))}

    @staticmethod
    def _financing_coverage(has_corrections: bool) -> Dict[str, Any]:
        return {
            "decision": True,
            "correction": has_corrections,
            "price_confirmation": False,
            "payment_completion": False,
            "issuance_result": False,
            "withdrawal": False,
            "reason_code": "financing_followup_filings_not_in_corpus",
        }

    @staticmethod
    def _date_key(value: Optional[str]) -> str:
        return "".join(re.findall(r"\d", value or ""))[:8]

    def find_contract_lifecycle(self, plan: Dict[str, Any], limit: int = 200) -> Dict[str, Any]:
        companies = plan.get("companies") or []
        years = plan.get("years") or []
        if not companies or not years:
            return {"contracts": [], "terminations": [], "matches": []}
        corp_codes = [company["corp_code"] for company in companies]
        contract_rows = self.conn.execute(
            f"""SELECT e.event_id,e.event_type,d.doc_id,d.corp_code,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,
                       d.is_correction,d.original_doc_id,d.supersedes_doc_id,d.superseded_by_doc_id,d.is_latest_version
                  FROM events e JOIN documents d ON d.doc_id=e.doc_id
                 WHERE e.event_type='단일판매공급계약체결'
                   AND d.corp_code IN ({','.join('?' for _ in corp_codes)})
                 ORDER BY d.rcept_dt,e.event_id LIMIT ?""", corp_codes + [limit * 5]
        ).fetchall()
        termination_rows = self.conn.execute(
            f"""SELECT e.event_id,e.event_type,d.doc_id,d.corp_code,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,
                       d.is_correction,d.original_doc_id,d.supersedes_doc_id,d.superseded_by_doc_id,d.is_latest_version
                  FROM events e JOIN documents d ON d.doc_id=e.doc_id
                 WHERE e.event_type='단일판매공급계약해지'
                   AND d.corp_code IN ({','.join('?' for _ in corp_codes)})
                   AND substr(d.rcept_dt,1,4)>=?
                 ORDER BY d.rcept_dt,e.event_id LIMIT ?""", corp_codes + [str(min(years)), limit]
        ).fetchall()
        contract_versions = [self._contract_event(dict(row), termination=False) for row in contract_rows]
        terminations = [self._contract_event(dict(row), termination=True) for row in termination_rows]
        grouped: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
        for contract in contract_versions:
            fallback = (
                self._date_key(contract.get("contract_date")),
                self._normalize_match_text(contract.get("contract_name")),
                self._normalize_match_text(contract.get("counterparty")),
            )
            root = contract.get("original_doc_id") or ("|".join(fallback) if any(fallback) else contract.get("doc_id"))
            grouped[(contract.get("corp_code"), root)].append(contract)
        chains: List[Dict[str, Any]] = []
        contracts: List[Dict[str, Any]] = []
        target_years = {str(year) for year in years}
        for (corp_code, root), versions in grouped.items():
            versions.sort(key=lambda item: (item.get("rcept_dt") or "", item.get("rcept_no") or ""))
            current = versions[-1]
            initial = versions[0]
            contract_year = self._date_key(initial.get("contract_date"))[:4] or (initial.get("rcept_dt") or "")[:4]
            if target_years and contract_year not in target_years:
                continue
            chain_id = f"contract:{corp_code}:{root}"
            for version in versions:
                version["contract_chain_id"] = chain_id
                version["stage"] = "correction" if version.get("is_correction") else "contract"
            current["history"] = versions
            current["contract_chain_id"] = chain_id
            contracts.append(current)
            chains.append({"contract_chain_id": chain_id, "initial": initial, "current": current,
                           "history": versions, "terminations": [], "status": "active_no_termination_found"})

        matches: List[Dict[str, Any]] = []
        for termination in terminations:
            scored = []
            for chain in chains:
                if chain["current"].get("corp_code") != termination.get("corp_code"):
                    continue
                score, reasons = self._contract_match_score(chain, termination)
                if score:
                    scored.append((score, chain["contract_chain_id"], reasons, chain))
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            if not scored:
                continue
            best_score, _, reasons, chain = scored[0]
            second_score = scored[1][0] if len(scored) > 1 else 0
            if best_score < 4 or best_score == second_score:
                continue
            contract = chain["current"]
            termination_kind = self._termination_kind(contract.get("amount_krw"), termination.get("amount_krw"))
            confidence = "high" if best_score >= 9 else "medium"
            match = {"contract": contract, "termination": termination, "contract_chain_id": chain["contract_chain_id"],
                     "match_score": best_score, "match_reasons": reasons, "match_confidence": confidence,
                     "termination_kind": termination_kind}
            matches.append(match)
            chain["terminations"].append(termination)
            chain["status"] = "partially_terminated" if termination_kind == "partial" else "terminated"
        return {"contracts": contracts, "terminations": terminations, "matches": matches, "chains": chains,
                "unmatched_terminations": [item for item in terminations if item.get("event_id") not in
                                            {match["termination"].get("event_id") for match in matches}]}

    def find_business_change_evidence(self, plan: Dict[str, Any], per_year: int = 8) -> Dict[str, Any]:
        candidate_doc_ids = plan.get("_candidate_doc_ids") or []
        if not candidate_doc_ids:
            return {"evidence": [], "profiles": {}}
        rows = self.conn.execute(
            f"""SELECT c.chunk_id,c.doc_id,c.heading,c.section_path,c.text,d.corp_code,d.corp_name,d.report_nm,
                       d.rcept_no,d.rcept_dt,d.base_year,d.base_month
                  FROM chunks c JOIN documents d ON d.doc_id=c.doc_id
                 WHERE c.doc_id IN ({','.join('?' for _ in candidate_doc_ids)})
                   AND c.section_path LIKE '%II. 사업의 내용%'
                 ORDER BY d.base_year,c.chunk_id""", candidate_doc_ids
        ).fetchall()
        ranked: Dict[int, Dict[str, List[tuple[int, Dict[str, Any]]]]] = defaultdict(lambda: defaultdict(list))
        profiles: Dict[int, Dict[str, Any]] = defaultdict(
            lambda: {"categories": set(), "signals": set(), "topics": set(),
                     "signal_sources": defaultdict(list), "topic_sources": defaultdict(list),
                     "signal_counts": defaultdict(int), "topic_counts": defaultdict(int), "section_count": 0}
        )
        for raw in rows:
            item = dict(raw)
            year = int(item.get("base_year") or 0)
            haystack = (item.get("heading") or "") + " " + (item.get("section_path") or "") + " " + item.get("text", "")
            categories = self._business_categories(haystack)
            signals = self._business_signals(haystack, item.get("corp_name"))
            primary_business_section = ("사업의 개요" in (item.get("section_path") or "") or
                                        "주요 제품" in (item.get("section_path") or ""))
            topics = self._business_topics(haystack, item.get("corp_name")) if primary_business_section else []
            profiles[year]["categories"].update(categories)
            profiles[year]["signals"].update(signals)
            profiles[year]["topics"].update(topics)
            for signal in signals:
                profiles[year]["signal_sources"][signal].append(item["chunk_id"])
                profiles[year]["signal_counts"][signal] += 1
            for topic in topics:
                profiles[year]["topic_sources"][topic].append(item["chunk_id"])
                profiles[year]["topic_counts"][topic] += 1
            profiles[year]["section_count"] += 1
            item.update({"kind": "business_evidence", "record_id": item["chunk_id"],
                         "citation": self.citation_for("text", item["chunk_id"]),
                         "evidence_categories": categories, "signals": signals})
            for category in categories:
                heading_text = (item.get("heading") or "") + " " + (item.get("section_path") or "")
                score = 12 * sum(token.lower() in heading_text.lower() for token in BUSINESS_EVIDENCE_CATEGORIES[category])
                score += 3 * sum(token.lower() in haystack.lower() for token in BUSINESS_EVIDENCE_CATEGORIES[category])
                score += min(6, len(signals))
                score += 2 if 150 <= len(item.get("text") or "") <= 5000 else 0
                ranked[year][category].append((score, item))
        result: List[Dict[str, Any]] = []
        for year in sorted(ranked):
            selected_ids = set()
            for category in BUSINESS_EVIDENCE_CATEGORIES:
                candidates = ranked[year].get(category, [])
                candidates.sort(key=lambda pair: (-pair[0], pair[1]["chunk_id"]))
                chosen = next((item for _, item in candidates if item["chunk_id"] not in selected_ids), None)
                if chosen:
                    selected_ids.add(chosen["chunk_id"])
                    result.append(chosen)
                if len(selected_ids) >= per_year:
                    break
        normalized_profiles = {
            year: {
                "categories": sorted(profile["categories"]),
                "signals": sorted(profile["signals"]),
                "topics": sorted(profile.get("topics", set())),
                "signal_sources": {signal: list(dict.fromkeys(ids))[:5]
                                   for signal, ids in profile["signal_sources"].items()},
                "topic_sources": {topic: list(dict.fromkeys(ids))[:5]
                                  for topic, ids in profile["topic_sources"].items()},
                "signal_counts": dict(profile["signal_counts"]),
                "topic_counts": dict(profile["topic_counts"]),
                "section_count": profile["section_count"],
            }
            for year, profile in profiles.items()
        }
        return {"evidence": result, "profiles": normalized_profiles,
                "revenue_mix_changes": self._business_revenue_mix_changes(candidate_doc_ids, plan.get("years") or [])}

    def _business_revenue_mix_changes(self, doc_ids: List[str], years: List[int]) -> List[Dict[str, Any]]:
        """Extract only directly comparable segment revenue-share changes.

        A change is emitted only when both requested years appear in the same
        consolidated table and the row explicitly pairs ``매출액`` with ``비중``.
        This keeps qualitative keyword changes separate from audited numeric
        composition changes.
        """
        if len(years) < 2 or not doc_ids:
            return []
        old_year, new_year = min(years), max(years)
        tables = self.conn.execute(
            f"""SELECT t.table_id,t.doc_id,t.table_title,t.section_path,t.unit_json,d.base_year,d.rcept_no
                  FROM logical_tables t JOIN documents d ON d.doc_id=t.doc_id
                 WHERE t.doc_id IN ({','.join('?' for _ in doc_ids)})
                   AND t.scope='consolidated'
                   AND t.section_path LIKE '%II. 사업의 내용%'
                   AND (t.table_title LIKE '%매출 비중%' OR t.search_text LIKE '%매출액%비중%')
                 ORDER BY d.base_year DESC,t.table_id""", doc_ids
        ).fetchall()
        for table in tables:
            cells = [dict(row) for row in self.conn.execute(
                """SELECT row_index,column_index,row_label,original_text FROM cells
                     WHERE table_id=? ORDER BY row_index,column_index""", (table["table_id"],)
            ).fetchall()]
            matrix = {(cell["row_index"], cell["column_index"]): cell.get("original_text") or "" for cell in cells}
            year_columns: Dict[int, int] = {}
            for cell in cells:
                match = re.search(r"(20\d{2})년", cell.get("original_text") or "")
                if match:
                    year_columns[int(match.group(1))] = cell["column_index"]
            if old_year not in year_columns or new_year not in year_columns:
                continue
            changes: List[Dict[str, Any]] = []
            for row_index in sorted({cell["row_index"] for cell in cells}):
                row = [cell for cell in cells if cell["row_index"] == row_index]
                if not any((cell.get("original_text") or "").strip() == "매출액" for cell in row):
                    continue
                segment = next((cell.get("row_label") for cell in row if cell.get("row_label")), None)
                if not segment or segment in {"주요제품", "구분", "합계"}:
                    continue
                try:
                    old_share = Decimal(self._plain_number(matrix[(row_index, year_columns[old_year] + 1)]))
                    new_share = Decimal(self._plain_number(matrix[(row_index, year_columns[new_year] + 1)]))
                except (KeyError, InvalidOperation, ValueError):
                    continue
                changes.append({
                    "segment": segment, "old_year": old_year, "new_year": new_year,
                    "old_share": str(old_share), "new_share": str(new_share),
                    "change_pp": str(new_share - old_share), "unit": "%",
                    "table_id": table["table_id"], "citation": self.citation_for("table", table["table_id"]),
                })
            if changes:
                return changes
        return []

    def find_business_overview_evidence(self, plan: Dict[str, Any], limit: int = 4) -> List[Dict[str, Any]]:
        """Return concise, section-prioritized evidence for open business questions.

        The report's own ``1. 사업의 개요`` is the primary source.  Product/service
        sections are supplementary, which prevents a detailed price table from
        replacing the company-wide business description.
        """
        candidate_doc_ids = plan.get("_candidate_doc_ids") or []
        if not candidate_doc_ids:
            return []
        rows = self.conn.execute(
            f"""SELECT c.chunk_id,c.doc_id,c.heading,c.section_path,c.text,d.corp_code,d.corp_name,
                       d.report_nm,d.rcept_no,d.rcept_dt,d.base_year,d.base_month,d.doc_subtype
                  FROM chunks c JOIN documents d ON d.doc_id=c.doc_id
                 WHERE c.doc_id IN ({','.join('?' for _ in candidate_doc_ids)})
                   AND c.section_path LIKE '%II. 사업의 내용%'
                 ORDER BY d.base_month DESC,c.chunk_id""", candidate_doc_ids
        ).fetchall()
        question = plan.get("question") or ""
        focus_tokens = self._business_focus_tokens(question, plan.get("companies") or [])
        ranked = []
        for raw in rows:
            item = dict(raw)
            path = item.get("section_path") or ""
            text = item.get("text") or ""
            topics = self._business_topics(text, item.get("corp_name"))
            score = 0
            if re.search(r">\s*1\.\s*\([^)]*\)?사업의 개요|>\s*1\.\s*사업의 개요", path):
                score += 100
            elif "사업의 개요" in path:
                score += 80
            if "주요 제품 및 서비스" in path or "주요 제품" in path:
                score += 35
            if "연결회사의 각 부분" in text and "구분" in text:
                score += 35
            if any(token in text for token in ("사업별로 보면", "주력 제품", "주요 사업은", "주요 사업을")):
                score += 30
            if "금융업" in path and not any(token in question for token in ("금융", "카드", "보험")):
                score -= 20
            score += min(20, 3 * sum(token.lower() in (path + " " + text).lower() for token in focus_tokens))
            if any(token in question for token in ("차량", "자동차")) and "차량부문" in text:
                score += 50
            if any(token in question for token in ("포트폴리오", "주요 사업", "사업부문")):
                score += min(60, 12 * len(topics))
            strategy_intent = any(token in question for token in ("전략", "방향", "대응", "목표", "추진"))
            if strategy_intent:
                strategy_hits = sum(token in text for token in (
                    "2030", "중장기", "전략", "추진", "확대", "강화", "프리미어 얼라이언스", "시장 다각화",
                ))
                score += min(50, 8 * strategy_hits)
                if any(token in question for token in ("중장기", "2030")) and "2030" in text:
                    score += 100
            if len(text) >= 120:
                score += 5
            item.update({
                "kind": "business_overview",
                "record_id": item["chunk_id"],
                "citation": self.citation_for("text", item["chunk_id"]),
                "evidence_categories": self._business_categories(path + " " + text),
                "topics": topics,
            })
            ranked.append((score, item))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["chunk_id"]))
        return self._single_report_business_evidence(ranked, limit)

    @staticmethod
    def _single_report_business_evidence(ranked: List[tuple[int, Dict[str, Any]]],
                                         limit: int) -> List[Dict[str, Any]]:
        """Keep a concise overview and its citations inside one selected report."""
        if not ranked:
            return []
        newest_item = max(
            (item for _, item in ranked),
            key=lambda item: (
                int(item.get("base_year") or 0), int(item.get("base_month") or 0),
                str(item.get("rcept_dt") or ""), str(item.get("doc_id") or ""),
            ),
        )
        primary_doc_id = newest_item.get("doc_id")
        selected: List[Dict[str, Any]] = []
        seen_paths = set()
        for _, item in ranked:
            if item.get("doc_id") != primary_doc_id:
                continue
            exact_path = item.get("section_path") or ""
            if exact_path in seen_paths:
                continue
            selected.append(item)
            seen_paths.add(exact_path)
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _business_focus_tokens(question: str, companies: Optional[List[Dict[str, Any]]] = None) -> List[str]:
        """Keep subject terms while dropping evaluation/instruction wrappers."""
        ignored_stems = (
            "교차검증", "공시", "분기보고서", "사업보고서", "사업의", "내용", "근거",
            "핵심", "항목별", "답해", "정리", "설명", "요약", "기준", "외부",
            "지식", "표지", "문구", "제외", "추측", "정답", "표기", "흔들림",
            "수치", "단위", "접수번호", "빠짐없이", "제시", "확인",
        )
        tokens = re.findall(r"[0-9A-Za-z가-힣]+", question)
        company_terms = set()
        for company in companies or []:
            names = [company.get(key) for key in ("corp_name", "listed_name", "corp_eng_name", "stock_code")]
            names.extend(COMPANY_ALIASES.get(company.get("corp_code") or "", ()))
            for value in tuple(names):
                names.extend(COMPANY_NAME_ALIASES.get(value or "", ()))
            for name in names:
                compact = re.sub(r"[^0-9A-Za-z가-힣]", "", str(name or "")).casefold()
                if compact:
                    company_terms.add(compact)

        def company_token(token: str) -> bool:
            compact = token.casefold()
            compact = re.sub(r"(?:에서|에게|보다|으로|은|는|이|가|의|을|를|와|과|로)$", "", compact)
            return bool(compact) and any(compact in term or term in compact for term in company_terms)

        return [token for token in tokens if len(token) >= 2 and not token.casefold() == "ii"
                and (token == "2030" or not re.fullmatch(
                    r"(?:20)?\d{2}년?|\d분기|\d{6}|q[1-4]", token, re.IGNORECASE
                ))
                and not company_token(token)
                and not any(token.startswith(stem) for stem in ignored_stems)]

    def find_business_document_evidence(self, plan: Dict[str, Any], per_type: int = 8) -> Dict[str, Any]:
        """Build comparable business profiles for annual vs quarterly filings."""
        candidate_doc_ids = plan.get("_candidate_doc_ids") or []
        if not candidate_doc_ids:
            return {"evidence": [], "profiles": {}}
        document_rows = self.conn.execute(
            f"""SELECT doc_id,doc_subtype,base_month,rcept_dt FROM documents
                 WHERE doc_id IN ({','.join('?' for _ in candidate_doc_ids)})
                   AND doc_subtype IN ('annual','quarter')""", candidate_doc_ids
        ).fetchall()
        # An unspecified "quarterly report" means the latest available quarter
        # in that base year.  Mixing Q1 and Q3 duplicates the same prose and can
        # falsely turn one repeated keyword into a strong change signal.
        selected_doc_ids = []
        for subtype in ("annual", "quarter"):
            choices = [row for row in document_rows if row["doc_subtype"] == subtype]
            if choices:
                selected_doc_ids.append(max(choices, key=lambda row: (
                    row["base_month"] or 0, row["rcept_dt"] or "", row["doc_id"]
                ))["doc_id"])
        if len(selected_doc_ids) < 2:
            return {"evidence": [], "profiles": {}}
        rows = self.conn.execute(
            f"""SELECT c.chunk_id,c.doc_id,c.heading,c.section_path,c.text,d.corp_code,d.corp_name,d.report_nm,
                       d.rcept_no,d.rcept_dt,d.base_year,d.base_month,d.doc_subtype
                  FROM chunks c JOIN documents d ON d.doc_id=c.doc_id
                 WHERE c.doc_id IN ({','.join('?' for _ in selected_doc_ids)})
                   AND c.section_path LIKE '%II. 사업의 내용%'
                   AND d.doc_subtype IN ('annual','quarter')
                 ORDER BY d.doc_subtype,d.base_month DESC,c.chunk_id""", selected_doc_ids
        ).fetchall()
        ranked: Dict[str, Dict[str, List[tuple[int, Dict[str, Any]]]]] = defaultdict(lambda: defaultdict(list))
        profiles: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"categories": set(), "signals": set(), "topics": set(),
                     "signal_counts": defaultdict(int), "topic_counts": defaultdict(int)}
        )
        for raw in rows:
            item = dict(raw); subtype = item.get("doc_subtype")
            haystack = (item.get("heading") or "") + " " + item.get("section_path", "") + " " + item.get("text", "")
            categories = self._business_categories(haystack)
            signals = self._business_signals(haystack, item.get("corp_name"))
            primary_business_section = ("사업의 개요" in (item.get("section_path") or "") or
                                        "주요 제품" in (item.get("section_path") or ""))
            topics = self._business_topics(haystack, item.get("corp_name")) if primary_business_section else []
            profiles[subtype]["categories"].update(categories); profiles[subtype]["signals"].update(signals)
            profiles[subtype]["topics"].update(topics)
            for signal in signals:
                profiles[subtype]["signal_counts"][signal] += 1
            for topic in topics:
                profiles[subtype]["topic_counts"][topic] += 1
            item.update({"kind": "business_evidence", "record_id": item["chunk_id"],
                         "citation": self.citation_for("text", item["chunk_id"]),
                         "evidence_categories": categories, "signals": signals})
            for category in categories:
                aliases = BUSINESS_EVIDENCE_CATEGORIES[category]
                score = 8 * sum(alias.lower() in ((item.get("heading") or "") + " " + item.get("section_path", "")).lower()
                                for alias in aliases)
                score += 2 * sum(alias.lower() in haystack.lower() for alias in aliases) + min(6, len(signals))
                ranked[subtype][category].append((score, item))
        evidence: List[Dict[str, Any]] = []
        for subtype in ("annual", "quarter"):
            selected = set()
            for category in BUSINESS_EVIDENCE_CATEGORIES:
                options = sorted(ranked[subtype].get(category, []), key=lambda pair: (-pair[0], pair[1]["chunk_id"]))
                chosen = next((item for _, item in options if item["chunk_id"] not in selected), None)
                if chosen:
                    selected.add(chosen["chunk_id"]); evidence.append(chosen)
                if len(selected) >= per_type:
                    break
        normalized = {subtype: {"categories": sorted(value["categories"]), "signals": sorted(value["signals"]),
                                "topics": sorted(value["topics"]),
                                "signal_counts": dict(value["signal_counts"]),
                                "topic_counts": dict(value["topic_counts"])}
                      for subtype, value in profiles.items()}
        return {"evidence": evidence, "profiles": normalized}

    @staticmethod
    def _business_categories(text: str) -> List[str]:
        lowered = text.lower()
        return [category for category, aliases in BUSINESS_EVIDENCE_CATEGORIES.items()
                if any(alias.lower() in lowered for alias in aliases)] or ["overview"]

    @staticmethod
    def _business_signals(text: str, corp_name: Optional[str] = None) -> List[str]:
        lowered = text.lower()
        signals = [signal for signal, aliases in BUSINESS_SIGNALS.items()
                   if any(alias.lower() in lowered for alias in aliases)]
        if corp_name and not any(token in corp_name for token in ("현대자동차", "기아")):
            signals = [signal for signal in signals if signal not in AUTOMOTIVE_ONLY_SIGNALS]
        return signals

    @staticmethod
    def _business_topics(text: str, corp_name: Optional[str] = None) -> List[str]:
        lowered = text.lower()
        topics = [topic for topic, aliases in BUSINESS_TOPICS.items()
                  if any(alias.lower() in lowered for alias in aliases)]
        if corp_name and not any(token in corp_name for token in ("현대자동차", "기아")):
            topics = [topic for topic in topics if topic not in AUTOMOTIVE_ONLY_TOPICS]
        if corp_name and not any(token in corp_name for token in ("케이티", "KT", "LG유플러스", "SK텔레콤", "LG헬로비전")):
            topics = [topic for topic in topics if topic not in TELECOM_ONLY_TOPICS]
        return topics

    def find_event_fields(self, plan: Dict[str, Any], limit: int = 20) -> List[Dict[str, Any]]:
        return self.find_event_fields_for_metric(plan, plan.get("metric"), limit=limit)

    def find_event_fields_for_metric(self, plan: Dict[str, Any], metric: Optional[str], limit: int = 20) -> List[Dict[str, Any]]:
        key_map = {"contract_amount": ["contract_amount_krw"], "contract_ratio": ["revenue_ratio_pct"],
                   "holding_ratio": ["holding_ratio_pct"],
                   "equity": ["equity_krw"]}
        keys = key_map.get(metric or "", [])
        if not keys:
            return []
        where = ["ef.field_key IN (" + ",".join("?" for _ in keys) + ")"]
        args: List[Any] = list(keys)
        candidate_doc_ids = plan.get("_candidate_doc_ids") or []
        if candidate_doc_ids:
            where.append("d.doc_id IN (" + ",".join("?" for _ in candidate_doc_ids) + ")")
            args.extend(candidate_doc_ids)
        companies = plan.get("companies") or []
        if companies:
            where.append("d.corp_code IN (" + ",".join("?" for _ in companies) + ")")
            args.extend(company["corp_code"] for company in companies)
        if plan.get("doc_groups"):
            where.append("d.doc_group IN (" + ",".join("?" for _ in plan["doc_groups"]) + ")")
            args.extend(plan["doc_groups"])
        years = self._candidate_years(plan)
        if years:
            where.append("substr(d.rcept_dt,1,4) IN (" + ",".join("?" for _ in years) + ")")
            args.extend(str(year) for year in years)
        if plan.get("months"):
            where.append("cast(substr(d.rcept_dt,5,2) AS INTEGER) IN (" + ",".join("?" for _ in plan["months"]) + ")")
            args.extend(plan["months"])
        if plan.get("requires_current_effective", True):
            where.append("d.is_latest_version=1")
        args.append(limit)
        rows = self.conn.execute(
            f"""SELECT ef.*,e.event_title,e.event_type,d.doc_id,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,
                       d.doc_group,d.is_latest_version
                FROM event_fields ef JOIN events e ON e.event_id=ef.event_id
                JOIN documents d ON d.doc_id=e.doc_id WHERE {' AND '.join(where)}
                ORDER BY d.rcept_dt DESC,ef.ordinal LIMIT ?""", args).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            row["metric"] = metric
            citation = self.citation_for("event", row["event_id"])
            citation.update({"field_key": row.get("field_key"), "label": row.get("label"),
                             "original_text": row.get("original_text")})
            if row.get("locator_json"):
                try:
                    citation["source_locator"] = json.loads(row["locator_json"])
                except json.JSONDecodeError:
                    citation["source_locator"] = {"raw": row["locator_json"]}
            row["citation"] = citation
        return result

    def extract_structured_values(self, plan: Dict[str, Any], limit_per_metric: int = 30) -> Dict[str, Any]:
        metrics = plan.get("required_metrics") or ([plan["metric"]] if plan.get("metric") else [])
        result: Dict[str, Any] = {"cells": [], "event_fields": [], "missing_metrics": []}
        for metric in dict.fromkeys(metrics):
            cells = self.find_metric_cells_for_metric(plan, metric, limit=limit_per_metric) if metric in FINANCIAL_CELL_METRICS else []
            fields = self.find_event_fields_for_metric(plan, metric, limit=limit_per_metric)
            if cells:
                result["cells"].extend(cells)
            if fields:
                result["event_fields"].extend(fields)
            if not cells and not fields:
                result["missing_metrics"].append(metric)
        return result

    def correction_history(self, corp_code: str, limit: int = 20,
                           doc_groups: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        where = ["d.corp_code=?"]
        args: List[Any] = [corp_code]
        if doc_groups:
            where.append("d.doc_group IN (" + ",".join("?" for _ in doc_groups) + ")")
            args.extend(doc_groups)
        args.append(limit)
        rows = self.conn.execute(
            f"""SELECT d.doc_id,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,c.correction_date,c.original_filing_date,
                      ci.item,ci.reason,ci.before_text,ci.after_text,ci.effective_text,ci.locator_json,c.correction_id,
                      c.is_latest_version AS correction_is_latest
               FROM corrections c JOIN documents d ON d.doc_id=c.doc_id
               LEFT JOIN correction_items ci ON ci.correction_id=c.correction_id
               WHERE {' AND '.join(where)} ORDER BY d.rcept_dt DESC LIMIT ?""", args).fetchall()
        return [dict(row) for row in rows]

    def find_correction_chains(self, plan: Dict[str, Any], limit: int = 50) -> Dict[str, Any]:
        """Return original/before/after/current views across all disclosure groups."""
        companies = plan.get("companies") or []
        if not companies:
            return {"chains": [], "unlinked_count": 0}
        where = ["d.corp_code IN (" + ",".join("?" for _ in companies) + ")"]
        args: List[Any] = [company["corp_code"] for company in companies]
        if plan.get("doc_groups"):
            where.append("d.doc_group IN (" + ",".join("?" for _ in plan["doc_groups"]) + ")")
            args.extend(plan["doc_groups"])
        if plan.get("years"):
            where.append("substr(d.rcept_dt,1,4) IN (" + ",".join("?" for _ in plan["years"]) + ")")
            args.extend(str(year) for year in plan["years"])
        report_tokens = self._correction_report_tokens(plan.get("question") or "")
        if report_tokens:
            where.append("(" + " OR ".join("d.report_nm LIKE ?" for _ in report_tokens) + ")")
            args.extend(f"%{token}%" for token in report_tokens)
        rows = self.conn.execute(
            f"""SELECT d.doc_id,d.corp_code,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,d.doc_group,d.doc_subtype,
                       d.base_year,d.base_month,d.original_doc_id AS document_original_doc_id,d.supersedes_doc_id,
                       d.superseded_by_doc_id,d.is_latest_version,c.correction_id,c.original_doc_id,
                       c.original_filing_date,c.target_document,c.correction_date,ci.item_id,ci.item,ci.reason,
                       ci.before_text,ci.after_text,ci.effective_text,ci.locator_json
                  FROM corrections c JOIN documents d ON d.doc_id=c.doc_id
                  LEFT JOIN correction_items ci ON ci.correction_id=c.correction_id
                 WHERE {' AND '.join(where)} ORDER BY d.rcept_dt,d.rcept_no,ci.item_id LIMIT ?""",
            args + [limit * 100],
        ).fetchall()
        documents: Dict[str, Dict[str, Any]] = {}
        for raw in rows:
            row = dict(raw)
            document = documents.setdefault(row["doc_id"], {key: row.get(key) for key in (
                "doc_id", "corp_code", "corp_name", "report_nm", "rcept_no", "rcept_dt", "doc_group", "doc_subtype",
                "base_year", "base_month", "document_original_doc_id", "supersedes_doc_id", "superseded_by_doc_id",
                "is_latest_version", "correction_id", "original_doc_id", "original_filing_date", "target_document",
                "correction_date",
            )})
            document.setdefault("items", [])
            if row.get("item_id"):
                item = {key: row.get(key) for key in ("item_id", "item", "reason", "before_text", "after_text", "effective_text", "locator_json")}
                item["citation"] = {"doc_id": row.get("doc_id"), "corp_name": row.get("corp_name"),
                                    "report_nm": row.get("report_nm"), "rcept_no": row.get("rcept_no"),
                                    "rcept_dt": row.get("rcept_dt"), "correction_id": row.get("correction_id")}
                document["items"].append(item)

        grouped: Dict[tuple, Dict[str, Any]] = {}
        for document in documents.values():
            root, confidence = self._correction_root(document)
            key = (document.get("corp_code"), document.get("doc_group"), root)
            chain = grouped.setdefault(key, {"chain_id": f"correction:{document.get('corp_code')}:{document.get('doc_group')}:{root}",
                                             "root": root, "corp_code": document.get("corp_code"),
                                             "doc_group": document.get("doc_group"),
                                             "link_confidence": confidence, "versions": []})
            chain["versions"].append(document)
            if confidence == "low":
                chain["link_confidence"] = "low"
        chains: List[Dict[str, Any]] = []
        for chain in grouped.values():
            chain["versions"].sort(key=lambda item: (item.get("rcept_dt") or "", item.get("rcept_no") or ""))
            current = chain["versions"][-1]
            root_id = current.get("original_doc_id") or current.get("document_original_doc_id")
            original = self._document_summary(root_id) if root_id else self._find_event_chain_original(chain)
            effective_by_item: Dict[str, Dict[str, Any]] = {}
            for version in chain["versions"]:
                for item in version.get("items", []):
                    item_key = self._normalize_match_text(item.get("item")) or item.get("item_id")
                    state = effective_by_item.setdefault(item_key, {
                        "item": item.get("item"), "original": item.get("before_text"), "current": item.get("after_text"),
                        "reason": item.get("reason"), "citation": item.get("citation"), "history": [],
                    })
                    if state.get("original") is None:
                        state["original"] = item.get("before_text")
                    state["item"] = item.get("item") or state.get("item")
                    if (item.get("after_text") or "").strip():
                        state.update({"current": item.get("after_text"), "reason": item.get("reason"),
                                      "citation": item.get("citation")})
                    state["history"].append({"before": item.get("before_text"), "after": item.get("after_text"),
                                             "reason": item.get("reason"), "rcept_no": version.get("rcept_no"),
                                             "rcept_dt": version.get("rcept_dt")})
            chain.update({"original": original, "current_version": current,
                          "effective_items": list(effective_by_item.values())})
            chains.append(chain)
        chains.sort(key=lambda item: item["current_version"].get("rcept_dt") or "", reverse=True)
        return {"chains": chains[:limit], "unlinked_count": sum(chain["link_confidence"] == "low" for chain in chains)}

    def _find_event_chain_original(self, chain: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        root = chain.get("root") or ""
        if not root.startswith("event:"):
            return None
        _, event_type, identity_date = root.split(":", 2)
        rows = self.conn.execute(
            """SELECT d.doc_id,e.event_id FROM documents d JOIN events e ON e.doc_id=d.doc_id
                WHERE d.corp_code=? AND d.doc_group=? AND d.is_correction=0 AND e.event_type=? ORDER BY d.rcept_dt""",
            (chain.get("corp_code"), chain.get("doc_group"), event_type),
        ).fetchall()
        for row in rows:
            fields = self._event_fields(row["event_id"])
            value = self._field_value(fields, ("이사회결의일", "결정일", "계약(수주)일자", "계약(수주)일"))
            if self._date_key(value) == identity_date:
                return self._document_summary(row["doc_id"])
        return None

    def _correction_root(self, document: Dict[str, Any]) -> tuple[str, str]:
        explicit = document.get("original_doc_id") or document.get("document_original_doc_id")
        if explicit:
            return explicit, "high"
        event = self.conn.execute("SELECT event_id,event_type FROM events WHERE doc_id=? ORDER BY event_id LIMIT 1",
                                  (document["doc_id"],)).fetchone()
        if event:
            fields = self._event_fields(event["event_id"])
            identity_date = self._field_value(fields, ("이사회결의일", "결정일", "계약(수주)일자", "계약(수주)일"))
            if identity_date:
                return f"event:{event['event_type']}:{self._date_key(identity_date)}", "medium"
        if document.get("original_filing_date"):
            report = self._normalize_match_text(document.get("target_document") or document.get("report_nm"))
            return f"filing:{self._date_key(document['original_filing_date'])}:{report}", "medium"
        if document.get("doc_group") == "periodic" and document.get("base_year"):
            return f"periodic:{document.get('doc_subtype')}:{document.get('base_year')}:{document.get('base_month')}", "medium"
        return document["doc_id"], "low"

    def _document_summary(self, doc_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not doc_id:
            return None
        row = self.conn.execute(
            "SELECT doc_id,corp_name,report_nm,rcept_no,rcept_dt,doc_group,doc_subtype,base_year,base_month FROM documents WHERE doc_id=?",
            (doc_id,),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _correction_report_tokens(question: str) -> List[str]:
        groups = {
            "유상증자": ("유상증자",), "전환사채": ("전환사채", "CB"),
            "신주인수권부사채": ("신주인수권부사채", "BW"), "교환사채": ("교환사채", "EB"),
            "단일판매": ("공급계약", "판매계약", "주요 계약", "위탁생산"), "사업보고서": ("사업보고서",),
            "반기보고서": ("반기보고서",), "분기보고서": ("분기보고서",),
        }
        compact = question.upper()
        return [report_token for report_token, aliases in groups.items() if any(alias.upper() in compact for alias in aliases)]

    def _effective_table_unit(self, table: Dict[str, Any]) -> Dict[str, Any]:
        unit = self._json_dict(table.get("unit_json"))
        if unit.get("raw") and unit.get("scale") is not None:
            return unit
        candidates = self.conn.execute(
            """SELECT table_id,section_path,table_title,unit_json FROM logical_tables
                WHERE doc_id=? AND unit_json IS NOT NULL ORDER BY table_id""", (table["doc_id"],)
        ).fetchall()
        target_ordinal = self._table_ordinal(table["table_id"])
        best: Optional[tuple[int, Dict[str, Any]]] = None
        for row in candidates:
            candidate = self._json_dict(row["unit_json"])
            if not candidate.get("raw") or candidate.get("scale") is None:
                continue
            distance = target_ordinal - self._table_ordinal(row["table_id"])
            if not 0 <= distance <= 2:
                continue
            if row["section_path"] != table.get("section_path"):
                continue
            title_bonus = 1 if row["table_title"] == table.get("table_title") else 0
            rank = distance * 10 - title_bonus
            if best is None or rank < best[0]:
                best = (rank, candidate)
        return best[1] if best else {"raw": None, "currency": None, "scale": None, "quantity": "unknown"}

    def _event_fields(self, event_id: str) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM event_fields WHERE event_id=? ORDER BY ordinal", (event_id,)
        ).fetchall()]

    @staticmethod
    def _funding_amount(instrument: str, fields: List[Dict[str, Any]]) -> Optional[str]:
        if instrument in {"CB", "BW", "EB"}:
            field = next((field for field in fields if field.get("numeric_value") is not None
                          and "사채의 권면" in (field.get("label") or "")), None)
            return field.get("original_text") if field else None
        purpose_fields = [field for field in fields if field.get("label") and "자금조달의 목적" in field["label"]]
        amounts = []
        for field in purpose_fields:
            if field.get("numeric_value") is None:
                continue
            try:
                amounts.append(Decimal(HybridRetriever._plain_number(field.get("original_text"))))
            except (InvalidOperation, ValueError):
                continue
        if not amounts:
            return None
        return format(sum(amounts), "f")

    @staticmethod
    def _funding_purposes(fields: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        purposes: List[Dict[str, str]] = []
        pending: Optional[str] = None
        for field in fields:
            if "자금조달의 목적" not in (field.get("label") or ""):
                continue
            text = (field.get("original_text") or "").strip()
            if text.endswith("(원)") or text.endswith("자금"):
                pending = re.sub(r"\s*\(원\)\s*$", "", text)
            elif pending and text not in {"", "-"} and field.get("numeric_value") is not None:
                purposes.append({"purpose": pending, "amount_krw": HybridRetriever._plain_number(text)})
                pending = None
        return purposes

    @staticmethod
    def _field_value(fields: List[Dict[str, Any]], label_tokens: Sequence[str]) -> Optional[str]:
        field = next((field for field in fields if any(token in (field.get("label") or "") for token in label_tokens)), None)
        return field.get("original_text") if field else None

    def _contract_event(self, event: Dict[str, Any], termination: bool) -> Dict[str, Any]:
        fields = self._event_fields(event["event_id"])
        name_tokens = ("해지계약명", "계약명") if termination else ("계약내용", "계약명")
        amount_tokens = ("해지금액",) if termination else ("계약금액",)
        event.update({
            "kind": "contract_termination" if termination else "contract",
            "record_id": event["event_id"], "contract_name": self._field_value(fields, name_tokens),
            "amount_krw": self._field_value(fields, amount_tokens),
            "counterparty": self._field_value(fields, ("계약상대", "계약상대방")),
            "contract_date": self._field_value(fields, ("계약(수주)일자", "계약체결일")),
            "period_start": self._field_value(fields, ("계약기간 > 시작일",)),
            "period_end": self._field_value(fields, ("계약기간 > 종료일",)),
            "termination_date": self._field_value(fields, ("해지일자",)),
            "termination_reason": self._field_value(fields, ("해지 주요사유", "해지사유")),
            "related_disclosure": self._field_value(fields, ("관련공시",)),
            "citation": self.citation_for("event", event["event_id"]), "fields": fields,
        })
        return event

    def _contract_match_score(self, chain: Dict[str, Any], termination: Dict[str, Any]) -> tuple[int, List[str]]:
        """Score explicit identifiers first and reject ambiguous ties in the caller."""
        current = chain["current"]
        history = chain.get("history") or [current]
        score = 0
        reasons: List[str] = []
        related_compact = re.sub(r"\D", "", termination.get("related_disclosure") or "")
        related_digits = set(re.findall(r"20\d{6}", related_compact))
        version_dates = {(version.get("rcept_dt") or "")[:8] for version in history}
        if related_digits & version_dates:
            score += 10
            reasons.append("related_disclosure_date")
        name_score = self._name_overlap(current.get("contract_name"), termination.get("contract_name"))
        if name_score >= 0.75:
            score += 6
            reasons.append("contract_name")
        elif name_score >= 0.5:
            score += 4
            reasons.append("contract_name_partial")
        if (self._normalize_match_text(current.get("counterparty")) and
                self._normalize_match_text(current.get("counterparty")) == self._normalize_match_text(termination.get("counterparty"))):
            score += 3
            reasons.append("counterparty")
        if (self._date_key(current.get("period_start")) and
                self._date_key(current.get("period_start")) == self._date_key(termination.get("period_start"))):
            score += 2
            reasons.append("period_start")
        return score, reasons

    @staticmethod
    def _termination_kind(contract_amount: Optional[str], termination_amount: Optional[str]) -> str:
        try:
            contract = abs(Decimal(HybridRetriever._plain_number(contract_amount)))
            terminated = abs(Decimal(HybridRetriever._plain_number(termination_amount)))
        except (InvalidOperation, ValueError):
            return "unknown"
        if contract == 0:
            return "unknown"
        if terminated < contract:
            return "partial"
        if terminated == contract:
            return "total"
        return "unknown"

    @staticmethod
    def _normalize_match_text(value: Optional[str]) -> str:
        return re.sub(r"[^0-9A-Za-z가-힣]", "", value or "").lower()

    @staticmethod
    def _name_overlap(left: Optional[str], right: Optional[str]) -> float:
        def tokens(value: Optional[str]) -> set[str]:
            return {token for token in re.findall(r"[0-9A-Za-z가-힣]+", value or "") if len(token) > 1}
        a, b = tokens(left), tokens(right)
        return len(a & b) / max(1, min(len(a), len(b)))

    @staticmethod
    def _plain_number(value: Optional[str]) -> str:
        text = (value or "").strip().replace(",", "")
        negative = text.startswith(("△", "▲")) or (text.startswith("(") and text.endswith(")"))
        text = text.lstrip("△▲").strip().strip("()").rstrip("%").strip()
        if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
            raise ValueError("not numeric")
        return "-" + text.lstrip("+") if negative and not text.startswith("-") else text

    @staticmethod
    def _json_dict(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _json_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        try:
            parsed = json.loads(value or "[]")
            return [str(item) for item in parsed] if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def _last_json_value(value: Any) -> str:
        values = HybridRetriever._json_list(value)
        return values[-1] if values else ""

    @staticmethod
    def _table_ordinal(table_id: str) -> int:
        match = re.search(r":table:(\d+)$", table_id)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _allowed(item: Dict[str, Any], plan: Dict[str, Any]) -> bool:
        candidate_doc_ids = set(plan.get("_candidate_doc_ids") or [])
        if candidate_doc_ids and item.get("doc_id") not in candidate_doc_ids:
            return False
        companies = plan.get("companies") or []
        if companies and item.get("corp_name") not in {c["corp_name"] for c in companies}:
            return False
        if plan.get("doc_groups") and item.get("doc_group") not in plan["doc_groups"]:
            return False
        if plan.get("doc_subtypes") and item.get("doc_subtype") not in plan["doc_subtypes"]:
            return False
        years = HybridRetriever._candidate_years(plan)
        if years and item.get("base_year") and item["base_year"] not in years:
            return False
        if plan.get("requires_current_effective", True) and not item.get("is_latest_version"):
            return False
        return True

    @staticmethod
    def _score(item: Dict[str, Any], plan: Dict[str, Any]) -> float:
        # FTS5 bm25 is better when it is more negative.
        score = max(0.0, -float(item.get("rank") or 0))
        if plan.get("companies") and item.get("corp_name") in {c["corp_name"] for c in plan["companies"]}: score += 5
        if plan.get("doc_groups") and item.get("doc_group") in plan["doc_groups"]: score += 3
        if item.get("is_latest_version"): score += 1
        return score

    @staticmethod
    def _candidate_years(plan: Dict[str, Any]) -> List[int]:
        years = set(plan.get("years") or [])
        calculation = plan.get("calculation") or {}
        for key in ("target_year", "baseline_year"):
            if calculation.get(key):
                years.add(int(calculation[key]))
        return sorted(years)

    @staticmethod
    def _cell_score(row: Dict[str, Any], plan: Dict[str, Any], metric: Optional[str] = None) -> float:
        score = 0.0
        aliases = METRICS.get(metric or plan.get("metric"), [])
        if row.get("row_label") in aliases: score += 10
        elif any(alias in (row.get("row_label") or "") for alias in aliases): score += 6
        if plan.get("scope") == row.get("scope"): score += 5
        if row.get("period_role") == "current": score += 2
        column = " ".join(json.loads(row.get("column_path") or "[]"))
        requested_aggregation = plan.get("period_aggregation")
        if requested_aggregation:
            if row.get("period_aggregation") == requested_aggregation:
                score += 20
            elif row.get("period_aggregation") in {"three_month", "ytd"}:
                score -= 20
        elif plan.get("quarter") and any(token in column for token in ("3개월", "누적")):
            score += 2
        if row.get("is_latest_version"): score += 2
        if row.get("doc_subtype") == "annual": score += 2
        definition = metric_definition(metric or plan.get("metric"))
        if row.get("statement_type") in definition.get("statement_types", []):
            score += 8
        if metric == "capex":
            compact_label = re.sub(r"\s+", "", row.get("row_label") or "")
            if compact_label == "유형자산의취득": score += 25
            if any(token in compact_label for token in ("미지급금", "처분", "현물출자")): score -= 30
            if row.get("scope") == "consolidated" and not plan.get("scope"): score += 4
            if row.get("statement_type") == "cash_flow_statement" or "현금흐름표" in (row.get("table_title") or ""): score += 5
        return score

    def citation_for(self, kind: str, record_id: str) -> Dict[str, Any]:
        if kind == "cell":
            row = self.conn.execute(
                """SELECT d.doc_id,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,t.section_path,t.table_title,
                          c.row_label,c.column_path,c.original_text,c.unit_raw,c.cell_id,t.source_file,c.locator_json
                   FROM cells c JOIN logical_tables t ON t.table_id=c.table_id JOIN documents d ON d.doc_id=c.doc_id
                   WHERE c.cell_id=?""", (record_id,)).fetchone()
        elif kind == "table":
            row = self.conn.execute(
                """SELECT d.doc_id,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,t.section_path,t.table_title,t.table_id
                          ,t.source_file,t.locator_json
                   FROM logical_tables t JOIN documents d ON d.doc_id=t.doc_id WHERE t.table_id=?""", (record_id,)).fetchone()
        elif kind == "event":
            row = self.conn.execute(
                """SELECT d.doc_id,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,e.event_title,e.event_id
                   FROM events e JOIN documents d ON d.doc_id=e.doc_id WHERE e.event_id=?""", (record_id,)).fetchone()
        else:
            row = self.conn.execute(
                """SELECT d.doc_id,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,c.section_path,c.chunk_id
                          ,c.source_file,c.locator_json
                   FROM chunks c JOIN documents d ON d.doc_id=c.doc_id WHERE c.chunk_id=?""", (record_id,)).fetchone()
        if not row:
            return {"record_id": record_id}
        citation = dict(row)
        locator = citation.pop("locator_json", None)
        if locator:
            try:
                citation["source_locator"] = json.loads(locator)
            except json.JSONDecodeError:
                citation["source_locator"] = {"raw": locator}
        return citation

    def close(self) -> None:
        self.conn.close()
