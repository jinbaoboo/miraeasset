"""Metadata-filtered FTS retrieval and exact structured-cell lookup."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .query_analyzer import METRICS


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
    tokens = expansions + tokens
    return " OR ".join('"' + token.replace('"', '') + '"' for token in tokens[:20]) or '"공시"'


class HybridRetriever:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

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
            if plan.get("years"):
                where.append("(d.base_year IN (" + ",".join("?" for _ in plan["years"]) + ") OR "
                             "(d.base_year IS NULL AND substr(d.rcept_dt,1,4) IN (" +
                             ",".join("?" for _ in plan["years"]) + ")))")
                args.extend(plan["years"]); args.extend(str(year) for year in plan["years"])
            if plan.get("months"):
                where.append("(d.base_month IN (" + ",".join("?" for _ in plan["months"]) + ") OR "
                             "(d.base_month IS NULL AND cast(substr(d.rcept_dt,5,2) AS INTEGER) IN (" +
                             ",".join("?" for _ in plan["months"]) + ")))")
                args.extend(plan["months"]); args.extend(plan["months"])
            if plan.get("requires_current_effective", True):
                where.append("d.is_latest_version=1")
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
        seen = set(); result = []
        for item in candidates:
            key = (item["kind"], item["record_id"])
            if key not in seen:
                seen.add(key); result.append(item)
            if len(result) >= limit:
                break
        return result

    def find_metric_cells(self, plan: Dict[str, Any], limit: int = 30) -> List[Dict[str, Any]]:
        metric = plan.get("metric")
        aliases = METRICS.get(metric, [])
        if not aliases:
            return []
        where = ["c.numeric_value IS NOT NULL", "d.doc_group='periodic'"]
        args: List[Any] = []
        where.append("(" + " OR ".join("c.row_label LIKE ?" for _ in aliases) + ")")
        args.extend("%" + alias + "%" for alias in aliases)
        companies = plan.get("companies") or []
        if companies:
            where.append("d.corp_code IN (" + ",".join("?" for _ in companies) + ")")
            args.extend(company["corp_code"] for company in companies)
        if plan.get("years"):
            where.append("d.base_year IN (" + ",".join("?" for _ in plan["years"]) + ")")
            args.extend(plan["years"])
        if plan.get("doc_subtypes"):
            where.append("d.doc_subtype IN (" + ",".join("?" for _ in plan["doc_subtypes"]) + ")")
            args.extend(plan["doc_subtypes"])
        if plan.get("quarter"):
            where.append("d.base_month=?"); args.append(plan["quarter"] * 3)
        if plan.get("scope"):
            where.append("c.scope=?"); args.append(plan["scope"])
        if plan.get("requires_current_effective", True):
            where.append("d.is_latest_version=1")
        sql = f"""SELECT c.*,t.table_title,t.section_path,d.corp_code,d.corp_name,d.report_nm,d.rcept_no,d.rcept_dt,
                          d.base_year,d.base_month,d.doc_id,d.doc_subtype,d.is_latest_version
                   FROM cells c JOIN logical_tables t ON t.table_id=c.table_id
                   JOIN documents d ON d.doc_id=c.doc_id WHERE {' AND '.join(where)}
                   ORDER BY d.base_year DESC,d.base_month DESC,d.rcept_dt DESC LIMIT ?"""
        args.append(limit * 5)
        rows = [dict(row) for row in self.conn.execute(sql, args).fetchall()]
        for row in rows:
            row["selection_score"] = self._cell_score(row, plan)
            row["citation"] = self.citation_for("cell", row["cell_id"])
        rows.sort(key=lambda row: -row["selection_score"])
        return rows[:limit]

    def find_event_fields(self, plan: Dict[str, Any], limit: int = 20) -> List[Dict[str, Any]]:
        key_map = {"contract_amount": ["contract_amount_krw"], "contract_ratio": ["revenue_ratio_pct"],
                   "holding_ratio": ["holding_ratio_pct"],
                   "equity": ["equity_krw"]}
        keys = key_map.get(plan.get("metric"), [])
        if not keys:
            return []
        where = ["ef.field_key IN (" + ",".join("?" for _ in keys) + ")"]
        args: List[Any] = list(keys)
        companies = plan.get("companies") or []
        if companies:
            where.append("d.corp_code IN (" + ",".join("?" for _ in companies) + ")")
            args.extend(company["corp_code"] for company in companies)
        if plan.get("doc_groups"):
            where.append("d.doc_group IN (" + ",".join("?" for _ in plan["doc_groups"]) + ")")
            args.extend(plan["doc_groups"])
        if plan.get("years"):
            where.append("substr(d.rcept_dt,1,4) IN (" + ",".join("?" for _ in plan["years"]) + ")")
            args.extend(str(year) for year in plan["years"])
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

    @staticmethod
    def _allowed(item: Dict[str, Any], plan: Dict[str, Any]) -> bool:
        companies = plan.get("companies") or []
        if companies and item.get("corp_name") not in {c["corp_name"] for c in companies}:
            return False
        if plan.get("doc_groups") and item.get("doc_group") not in plan["doc_groups"]:
            return False
        if plan.get("doc_subtypes") and item.get("doc_subtype") not in plan["doc_subtypes"]:
            return False
        if plan.get("years") and item.get("base_year") and item["base_year"] not in plan["years"]:
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
    def _cell_score(row: Dict[str, Any], plan: Dict[str, Any]) -> float:
        score = 0.0
        aliases = METRICS.get(plan.get("metric"), [])
        if row.get("row_label") in aliases: score += 10
        elif any(alias in (row.get("row_label") or "") for alias in aliases): score += 6
        if plan.get("scope") == row.get("scope"): score += 5
        if row.get("period_role") == "current": score += 2
        column = " ".join(json.loads(row.get("column_path") or "[]"))
        if plan.get("quarter") and any(token in column for token in ("3개월", "누적")): score += 2
        if row.get("is_latest_version"): score += 2
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
