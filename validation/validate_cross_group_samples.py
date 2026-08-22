"""Cross-group validation on 20 deliberately varied filings."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from src.parser.disclosure_parser import DisclosureParser
from src.parser.periodic_parser import load_manifest


SAMPLES = {
    "periodic_20230515002335": ["quarter", "strict_recovery", "rowspan_colspan"],
    "periodic_20230814002534": ["half", "multilevel_header"],
    "periodic_20240312000736": ["annual", "audit_attachment", "complex_table_group"],
    "periodic_20240329002895": ["correction_annual", "before_after"],
    "periodic_20240913000803": ["correction_half", "different_company"],
    "periodic_20250320001103": ["mismatched_attribute_quote", "industrial", "audit_attachment"],
    "periodic_20260313001191": ["spaced_attribute_quote", "finance", "strict_recovery"],
    "periodic_20250318001196": ["quoted_acronym_attribute", "strict_recovery"],
    "periodic_20240514001522": ["pdf_fallback", "correction_quarter"],
    "major_20230103000001": ["capital_increase", "robotics"],
    "major_20240326000614": ["merger", "battery"],
    "major_20241118000171": ["correction_major", "treasury_stock"],
    "exchange_20250731800028": ["correction_exchange", "supply_contract"],
    "exchange_20251217800800": ["contract_termination", "battery"],
    "exchange_20240424800596": ["facility_investment", "semiconductor"],
    "exchange_20241220800005": ["free_form_management_event"],
    "exchange_20230315902426": ["shareholder_agreement", "robotics"],
    "holding_20230120000563": ["holding_general"],
    "holding_20230307000265": ["holding_short", "foreign_reporter"],
    "holding_20250107000540": ["correction_holding", "materials"],
}


def _check(rows: List[Dict[str, Any]], record: Dict[str, Any], item: str, ok: bool,
           problem: str = "", cause: str = "", needs_fix: bool = False) -> None:
    rows.append({"document": record["doc_id"], "test_item": item, "ok": bool(ok),
                 "problem": "" if ok else problem, "cause": "" if ok else cause,
                 "needs_fix": bool(needs_fix and not ok)})


def validate(data_root: Path, output_dir: Path) -> Dict[str, Any]:
    records = load_manifest(data_root / "manifest.jsonl")
    by_id = {record["doc_id"]: record for record in records}
    parser = DisclosureParser(data_root, records)
    checks: List[Dict[str, Any]] = []; documents: List[Dict[str, Any]] = []
    for doc_id, tags in SAMPLES.items():
        record = by_id[doc_id]
        result = parser.parse_document(record, include_attachments=True)
        status = result["parse_log"]["status"]
        _check(checks, record, "Document metadata", result["document"]["source"]["rcept_no"] == record["rcept_no"],
               "manifest/source mismatch", "metadata propagation", True)
        _check(checks, record, "Parse status", status != "failed", "; ".join(result["parse_log"]["errors"]), "parser error", True)
        _check(checks, record, "Body chunks", bool(result["text_chunks"]), "no searchable body", "empty extraction", True)
        if record["doc_group"] != "exchange":
            _check(checks, record, "Section hierarchy", bool(result["sections"]), "no sections", "section recognition", True)
        tables = result["logical_tables"]
        is_pdf_fallback = "pdf_fallback" in tags
        if is_pdf_fallback:
            locators = [chunk.get("source_locator", {}) for chunk in result["text_chunks"]]
            _check(checks, record, "PDF page locator", bool(locators and locators[0].get("page_number")),
                   "PDF page location missing", "PDF fallback", True)
            _check(checks, record, "PDF limitation warning",
                   any("pdf_fallback_text_only" in warning for warning in result["parse_log"].get("warnings", [])),
                   "PDF structure limitation not explicit", "PDF fallback", True)
        else:
            _check(checks, record, "Logical tables", bool(tables), "no logical tables", "table grouping", True)
            _check(checks, record, "Normalized cells", bool(result["table_cells"]), "no normalized cells", "grid parsing", True)
        valid_scope = all(table.get("scope") in {"consolidated", "separate", "unknown"} for table in tables)
        _check(checks, record, "Scope enum", valid_scope, "invalid scope", "scope inference", True)
        if record["is_correction"] and not is_pdf_fallback:
            _check(checks, record, "Correction before/after", bool(result["corrections"]),
                   "correction block not extracted", "non-standard correction layout", True)
        if record["doc_group"] != "periodic":
            _check(checks, record, "Event record", bool(result.get("events")), "event missing", "event normalization", True)
        if "audit_attachment" in tags:
            attachments = result["document"].get("attachments", [])
            _check(checks, record, "Audit attachment", bool(attachments), "attachment missing", "file role detection", True)
        numeric = next((cell for cell in result["table_cells"] if cell.get("numeric_value") is not None), None)
        narrative_number = any(any(char.isdigit() for char in chunk.get("body_text", "")) for chunk in result["text_chunks"])
        numeric_ok = bool((numeric and numeric.get("row_label") and numeric.get("table_id")) or (not numeric and narrative_number))
        _check(checks, record, "Numeric relation or narrative preservation", numeric_ok,
               "numeric content lacks cell relation and narrative preservation", "cell/text normalization", True)
        documents.append({"doc_id": doc_id, "corp_name": record["corp_name"], "doc_group": record["doc_group"],
                          "report_nm": record["report_nm"], "tags": tags, "status": status,
                          "counts": result["document"].get("record_counts", {}),
                          "warnings": result["parse_log"].get("warnings", [])[:5]})
    summary = Counter("pass" if row["ok"] else "fail" for row in checks)
    payload = {"sample_count": len(documents), "check_count": len(checks), "summary": dict(summary),
               "documents": documents, "checks": checks}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cross_group_validation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Cross-group parser validation", "", f"- Samples: {len(documents)}", f"- Checks: {len(checks)}",
             f"- Pass: {summary['pass']}", f"- Fail: {summary['fail']}", "",
             "| 문서 | 테스트 항목 | 정상 여부 | 문제 | 원인 | 수정 필요 여부 |", "|---|---|---:|---|---|---:|"]
    for row in checks:
        lines.append(f"| {row['document']} | {row['test_item']} | {'정상' if row['ok'] else '오류'} | {row['problem']} | {row['cause']} | {'예' if row['needs_fix'] else '아니오'} |")
    (output_dir / "cross_group_validation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, default=Path("validation/results")); args = ap.parse_args()
    result = validate(args.data_root, args.output_dir)
    print(json.dumps({key: result[key] for key in ("sample_count", "check_count", "summary")}, ensure_ascii=False, indent=2))
    return 1 if result["summary"].get("fail") else 0


if __name__ == "__main__":
    raise SystemExit(main())
