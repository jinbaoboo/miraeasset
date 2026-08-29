"""Audit stored periodic-table provenance against untouched raw XML files.

This is a read-only, deterministic spot check.  It does not reparse or modify
the corpus and is intended to catch DB/source drift before retrieval changes
are accepted.
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.parser.path_utils import resolve_manifest_path


TARGET_COMPANIES = (
    "LG이노텍", "삼성바이오로직스", "HMM", "HD현대일렉트릭", "POSCO홀딩스",
    "LG유플러스", "CJ제일제당", "셀트리온", "크래프톤", "현대건설",
)


def _json(value: Optional[str], fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _select_documents(conn: sqlite3.Connection, sample_size: int) -> List[Dict[str, Any]]:
    rows = [dict(row) for row in conn.execute(
        """SELECT d.doc_id,d.corp_name,d.doc_subtype,d.is_correction,
                  d.file_path,d.rcept_no,d.report_nm
           FROM documents d
           WHERE d.doc_group='periodic' AND d.file_format='xml' AND d.parse_status!='failed'
             AND EXISTS (SELECT 1 FROM logical_tables t WHERE t.doc_id=d.doc_id)
           ORDER BY d.corp_name,d.doc_subtype,d.is_correction DESC,d.doc_id"""
    )]
    selected: List[Dict[str, Any]] = []
    used = set()

    def add(row: Dict[str, Any]) -> None:
        if row["doc_id"] not in used and len(selected) < sample_size:
            used.add(row["doc_id"]); selected.append(row)

    # Pin unseen industries used by the QA audit.
    for company in TARGET_COMPANIES:
        candidate = next((row for row in rows if row["corp_name"] == company and
                          row["doc_subtype"] == "quarter" and "2025_03" in row["file_path"]), None)
        if candidate:
            add(candidate)
    # Preserve correction and subtype coverage.
    for subtype, correction in (("annual", 1), ("half", 1), ("quarter", 1),
                                 ("annual", 0), ("half", 0), ("quarter", 0)):
        for row in rows:
            if row["doc_subtype"] == subtype and row["is_correction"] == correction:
                add(row)
                if sum(x["doc_subtype"] == subtype and x["is_correction"] == correction for x in selected) >= 3:
                    break
    # Fill with distinct companies before allowing repeats.
    companies = {row["corp_name"] for row in selected}
    for row in rows:
        if row["corp_name"] not in companies:
            add(row); companies.add(row["corp_name"])
    for row in rows:
        add(row)
    return selected


def audit(db_path: Path, data_root: Path, sample_size: int = 30) -> Dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    documents = _select_documents(conn, sample_size)
    results: List[Dict[str, Any]] = []
    for document in documents:
        cell_row = conn.execute(
            """SELECT c.*,t.source_file,t.unit_json,t.columns_json,t.rows_json,
                      t.locator_json table_locator,t.statement_type,t.table_title,t.scope table_scope
               FROM cells c JOIN logical_tables t USING(table_id)
               WHERE c.doc_id=? AND c.numeric_value IS NOT NULL
                 AND length(trim(c.original_text))>=3 AND t.source_file_role='main'
               ORDER BY CASE WHEN t.statement_type IN
                    ('balance_sheet','income_statement','comprehensive_income_statement','cash_flow_statement')
                    THEN 0 ELSE 1 END,
                    CASE WHEN c.unit_raw IS NOT NULL AND c.row_label IS NOT NULL
                              AND c.column_path NOT IN ('[]','') THEN 0 ELSE 1 END,
                    c.cell_id LIMIT 1""",
            (document["doc_id"],),
        ).fetchone()
        source_dir = resolve_manifest_path(data_root, document["file_path"])
        source_path = source_dir / (cell_row["source_file"] if cell_row else f"{document['rcept_no']}.xml")
        checks = {
            "source_file_exists": source_path.is_file(),
            "numeric_cell_selected": cell_row is not None,
            "cell_locator_complete": False,
            "logical_table_structure": False,
            "scope_period_unit_relation": False,
            "original_text_in_raw_xml": False,
        }
        example: Dict[str, Any] = {}
        if cell_row:
            cell = dict(cell_row)
            cell_locator = _json(cell.get("locator_json"), {})
            table_locator = _json(cell.get("table_locator"), {})
            columns = _json(cell.get("columns_json"), [])
            rows = _json(cell.get("rows_json"), [])
            checks["cell_locator_complete"] = (
                isinstance(cell_locator.get("physical_table_ordinal"), int)
                and isinstance(cell.get("row_index"), int)
                and isinstance(cell.get("column_index"), int)
                and isinstance(table_locator.get("element_ordinal"), int)
            )
            checks["logical_table_structure"] = bool(columns and rows and cell.get("table_title"))
            checks["scope_period_unit_relation"] = bool(
                cell.get("row_label") and _json(cell.get("column_path"), [])
                and (cell.get("unit_raw") or _json(cell.get("unit_json"), {}).get("raw"))
                and cell.get("scope") in {"consolidated", "separate", "unknown"}
            )
            if source_path.is_file():
                raw = html.unescape(source_path.read_text(encoding="utf-8", errors="replace"))
                checks["original_text_in_raw_xml"] = cell["original_text"] in raw
            example = {key: cell.get(key) for key in (
                "cell_id", "table_id", "table_title", "statement_type", "row_label",
                "column_path", "original_text", "unit_raw", "scope", "period_aggregation",
            )}
        results.append({
            "doc_id": document["doc_id"], "corp_name": document["corp_name"],
            "report_nm": document["report_nm"], "doc_subtype": document["doc_subtype"],
            "is_correction": bool(document["is_correction"]),
            "source_path": f"{document['file_path']}/{source_path.name}",
            "passed": all(checks.values()), "checks": checks, "cell_example": example,
        })
    conn.close()
    by_subtype = Counter(row["doc_subtype"] for row in results)
    return {
        "database": str(db_path), "sample_size": len(results),
        "passed": sum(row["passed"] for row in results),
        "failed": sum(not row["passed"] for row in results),
        "coverage": {"by_subtype": dict(by_subtype),
                     "corrections": sum(row["is_correction"] for row in results),
                     "companies": len({row["corp_name"] for row in results})},
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--output", type=Path, default=Path("validation/results/source_locator_audit.json"))
    args = parser.parse_args()
    result = audit(args.db, args.data_root, args.sample_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = args.output.with_suffix(".md")
    lines = ["# 정기공시 DB-원문 근거 감사", "",
             f"- 표본: {result['sample_size']}건 / 통과: {result['passed']}건 / 실패: {result['failed']}건",
             f"- 기업: {result['coverage']['companies']}개 / 정정공시: {result['coverage']['corrections']}건",
             "- 원본 XML은 읽기만 하고 수정하지 않았다.", "",
             "| 문서 | 유형 | 정정 | 결과 | 실패 항목 |", "|---|---|---:|---|---|"]
    for row in result["results"]:
        failures = ", ".join(name for name, ok in row["checks"].items() if not ok) or "-"
        lines.append(f"| {row['corp_name']} {row['report_nm']} | {row['doc_subtype']} | "
                     f"{row['is_correction']} | {'통과' if row['passed'] else '실패'} | {failures} |")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("sample_size", "passed", "failed", "coverage")},
                     ensure_ascii=False, indent=2))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
