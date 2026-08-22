"""End-to-end relational, FTS, version, and privacy checks for the built DB."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.parser.periodic_parser import load_manifest


SENSITIVE = [
    re.compile(r"\b\d{6}[- ]?[1-4]\d{6}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{5}\b"),
    re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
]


def _check(checks: List[Dict[str, Any]], name: str, ok: bool, detail: Any, critical: bool = True) -> None:
    checks.append({"name": name, "ok": bool(ok), "critical": critical, "detail": detail})


def validate(db_path: Path, data_root: Optional[Path] = None) -> Dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    checks: List[Dict[str, Any]] = []
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    _check(checks, "sqlite_integrity", integrity == "ok", integrity)

    counts = {name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in (
        "documents", "sections", "chunks", "logical_tables", "cells", "corrections",
        "correction_items", "events", "event_fields", "chunks_fts", "tables_fts", "events_fts",
    )}
    _check(checks, "chunk_fts_count", counts["chunks"] == counts["chunks_fts"],
           {"records": counts["chunks"], "fts": counts["chunks_fts"]})
    _check(checks, "table_fts_count", counts["logical_tables"] == counts["tables_fts"],
           {"records": counts["logical_tables"], "fts": counts["tables_fts"]})
    _check(checks, "event_fts_count", counts["events"] == counts["events_fts"],
           {"records": counts["events"], "fts": counts["events_fts"]})

    orphan_queries = {
        "orphan_sections": "SELECT count(*) FROM sections s LEFT JOIN documents d USING(doc_id) WHERE d.doc_id IS NULL",
        "orphan_chunks": "SELECT count(*) FROM chunks c LEFT JOIN documents d USING(doc_id) WHERE d.doc_id IS NULL",
        "orphan_tables": "SELECT count(*) FROM logical_tables t LEFT JOIN documents d USING(doc_id) WHERE d.doc_id IS NULL",
        "orphan_cells": "SELECT count(*) FROM cells c LEFT JOIN logical_tables t USING(table_id) WHERE t.table_id IS NULL",
        "orphan_events": "SELECT count(*) FROM events e LEFT JOIN documents d USING(doc_id) WHERE d.doc_id IS NULL",
    }
    for name, sql in orphan_queries.items():
        value = conn.execute(sql).fetchone()[0]
        _check(checks, name, value == 0, value)

    failed = [dict(row) for row in conn.execute(
        "SELECT doc_id,doc_group,report_nm,errors_json FROM documents WHERE parse_status='failed' ORDER BY doc_group,doc_id"
    )]
    _check(checks, "failed_documents", not failed, failed)

    ambiguous_latest = [dict(row) for row in conn.execute(
        """SELECT corp_code,doc_subtype,base_year,base_month,sum(is_latest_version) latest_count,count(*) version_count
           FROM documents WHERE doc_group='periodic'
           GROUP BY corp_code,doc_subtype,base_year,base_month HAVING latest_count!=1"""
    )]
    _check(checks, "periodic_latest_version_uniqueness", not ambiguous_latest, ambiguous_latest)

    sensitive_hits = []
    for table, column in (("chunks", "text"), ("logical_tables", "search_text"), ("events", "search_text")):
        sql = f"""SELECT x.doc_id,x.{column} content FROM {table} x JOIN documents d ON d.doc_id=x.doc_id
                  WHERE d.doc_group='holding'"""
        for row in conn.execute(sql):
            if any(pattern.search(row["content"] or "") for pattern in SENSITIVE):
                sensitive_hits.append({"table": table, "doc_id": row["doc_id"]})
                if len(sensitive_hits) >= 20:
                    break
        if len(sensitive_hits) >= 20:
            break
    if len(sensitive_hits) < 20:
        for row in conn.execute(
            """SELECT d.doc_id,ef.original_text content FROM event_fields ef JOIN events e USING(event_id)
               JOIN documents d ON d.doc_id=e.doc_id WHERE d.doc_group='holding'"""
        ):
            if any(pattern.search(row["content"] or "") for pattern in SENSITIVE):
                sensitive_hits.append({"table": "event_fields", "doc_id": row["doc_id"]})
                if len(sensitive_hits) >= 20:
                    break
    _check(checks, "holding_search_text_sensitive_patterns", not sensitive_hits, sensitive_hits)

    if data_root is not None:
        manifest = load_manifest(Path(data_root) / "manifest.jsonl")
        expected = len(manifest)
        _check(checks, "manifest_document_count", counts["documents"] == expected,
               {"manifest": expected, "database": counts["documents"]})
        missing_paths = [record["doc_id"] for record in manifest
                         if not (Path(data_root) / record["file_path"]).exists()]
        _check(checks, "source_paths_exist", not missing_paths, missing_paths[:20])

    conn.close()
    failures = [check for check in checks if check["critical"] and not check["ok"]]
    return {"database": str(db_path), "counts": counts, "checks": checks,
            "summary": {"pass": len(checks) - len(failures), "fail": len(failures)}, "ok": not failures}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--data-root", type=Path)
    ap.add_argument("--output", type=Path, default=Path("validation/results/database_validation.json"))
    args = ap.parse_args()
    result = validate(args.db, args.data_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": result["ok"], "summary": result["summary"], "counts": result["counts"]},
                     ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
