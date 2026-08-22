"""Generate a compact parser/build quality report from SQLite."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict


def generate(db_path: Path) -> Dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    status = {row["parse_status"]: row["n"] for row in conn.execute("SELECT parse_status,count(*) n FROM documents GROUP BY parse_status")}
    groups = {row["doc_group"]: {"total": row["total"], "success": row["success"], "warning": row["warning"], "failed": row["failed"]}
              for row in conn.execute("""SELECT doc_group,count(*) total,sum(parse_status='success') success,
                                         sum(parse_status='warning') warning,sum(parse_status='failed') failed
                                      FROM documents GROUP BY doc_group""")}
    records = {}
    for name in ("companies", "documents", "sections", "chunks", "logical_tables", "cells", "corrections", "correction_items", "events", "event_fields"):
        records[name] = conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
    reasons = Counter()
    failed_documents = []
    warning_error_documents = []
    for row in conn.execute("SELECT doc_id,doc_group,report_nm,parse_status,warnings_json,errors_json FROM documents"):
        for warning in json.loads(row["warnings_json"] or "[]"):
            reasons[warning.split(":", 1)[-1].split(":", 1)[0]] += 1
        errors = json.loads(row["errors_json"] or "[]")
        if errors:
            target = failed_documents if row["parse_status"] == "failed" else warning_error_documents
            target.append({"doc_id": row["doc_id"], "doc_group": row["doc_group"],
                           "report_nm": row["report_nm"], "errors": errors})
    latest = conn.execute("SELECT count(*) FROM documents WHERE is_latest_version=1").fetchone()[0]
    linked = conn.execute("SELECT count(*) FROM documents WHERE original_doc_id IS NOT NULL").fetchone()[0]
    conn.close()
    return {"database": str(db_path), "status": status, "groups": groups, "records": records,
            "latest_documents": latest, "version_linked_documents": linked,
            "warning_reason_counts": dict(reasons.most_common()), "failed_documents": failed_documents,
            "warning_error_documents": warning_error_documents}


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--json-output", type=Path, default=Path("validation/results/corpus_quality.json"))
    ap.add_argument("--md-output", type=Path, default=Path("validation/results/corpus_quality.md")); args = ap.parse_args()
    result = generate(args.db); args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Corpus build quality", "", f"Database: `{result['database']}`", "", "## Document status", "",
             "| Group | Total | Success | Warning | Failed |", "|---|---:|---:|---:|---:|"]
    for group, value in result["groups"].items():
        lines.append(f"| {group} | {value['total']} | {value['success']} | {value['warning']} | {value['failed']} |")
    lines.extend(["", "## Record counts", "", "| Record | Count |", "|---|---:|"])
    for key, value in result["records"].items(): lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Failed documents", ""])
    if result["failed_documents"]:
        for item in result["failed_documents"]: lines.append(f"- `{item['doc_id']}`: {'; '.join(item['errors'])}")
    else: lines.append("- None")
    lines.extend(["", "## Warning documents with file-level errors", ""])
    if result["warning_error_documents"]:
        for item in result["warning_error_documents"]: lines.append(f"- `{item['doc_id']}`: {'; '.join(item['errors'])}")
    else: lines.append("- None")
    args.md_output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "groups": result["groups"], "records": result["records"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
