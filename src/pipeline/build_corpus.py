"""Resumable raw-corpus to SQLite build.

Examples:
  python -m src.pipeline.build_corpus --data-root /path/to/corpus --db outputs/disclosures.db --groups major exchange holding
  python -m src.pipeline.build_corpus --data-root /path/to/corpus --db outputs/disclosures.db --groups periodic
"""

from __future__ import annotations

import argparse
import fcntl
import json
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.parser.disclosure_parser import DisclosureParser
from src.parser.periodic_parser import load_manifest
from src.storage.sqlite_store import DisclosureStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _records(records: Iterable[Dict[str, Any]], groups: List[str], limit: Optional[int],
             doc_ids: Optional[List[str]] = None, corrections_only: bool = False) -> List[Dict[str, Any]]:
    selected_ids = set(doc_ids or [])
    chosen = [record for record in records if record.get("doc_group") in groups
              and (not selected_ids or record.get("doc_id") in selected_ids)
              and (not corrections_only or record.get("is_correction"))]
    return chosen[:limit] if limit else chosen


@contextmanager
def _exclusive_build_lock(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = db_path.with_suffix(db_path.suffix + ".build.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        lock_handle.close()
        raise RuntimeError(f"Another corpus build is already writing {db_path}") from error
    try:
        yield
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def run(data_root: Path, db_path: Path, groups: List[str], limit: Optional[int] = None,
        resume: bool = True, include_attachments: bool = True, progress_every: int = 25,
        doc_ids: Optional[List[str]] = None, corrections_only: bool = False,
        refresh_fts: bool = True) -> Dict[str, Any]:
    with _exclusive_build_lock(db_path):
        return _run_unlocked(data_root, db_path, groups, limit, resume, include_attachments,
                             progress_every, doc_ids, corrections_only, refresh_fts)


def _run_unlocked(data_root: Path, db_path: Path, groups: List[str], limit: Optional[int] = None,
                  resume: bool = True, include_attachments: bool = True, progress_every: int = 25,
                  doc_ids: Optional[List[str]] = None, corrections_only: bool = False,
                  refresh_fts: bool = True) -> Dict[str, Any]:
    records = load_manifest(data_root / "manifest.jsonl")
    chosen = _records(records, groups, limit, doc_ids, corrections_only)
    parser = DisclosureParser(data_root, records)
    store = DisclosureStore(db_path); store.initialize(); store.load_companies(data_root / "universe.csv")
    store.sync_manifest_metadata(records)
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    started = time.monotonic()
    stats: Counter[str] = Counter()
    store.conn.execute("UPDATE parse_runs SET status='interrupted',finished_at=? WHERE status='running'", (_now(),))
    store.conn.execute(
        "INSERT INTO parse_runs(run_id,started_at,status,requested_groups,details_json) VALUES (?,?,?,?,?)",
        (run_id, _now(), "running", json.dumps(groups, ensure_ascii=False), json.dumps({"limit": limit, "resume": resume})),
    ); store.conn.commit()
    log_path = db_path.with_suffix(".build.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        for position, record in enumerate(chosen, start=1):
            if resume and store.has_document(record["doc_id"]):
                stats["skipped"] += 1
                continue
            item_started = time.monotonic()
            try:
                result = parser.parse_document(record, include_attachments=include_attachments)
                store.upsert_result(record, result, refresh_fts=refresh_fts)
                status = result["parse_log"]["status"]
                stats[status] += 1
                details = {
                    "time": _now(), "run_id": run_id, "position": position, "total": len(chosen),
                    "doc_id": record["doc_id"], "doc_group": record.get("doc_group"), "status": status,
                    "seconds": round(time.monotonic() - item_started, 3),
                    "counts": result.get("document", {}).get("record_counts", {}),
                    "warnings": result["parse_log"].get("warnings", [])[:10],
                    "errors": result["parse_log"].get("errors", [])[:10],
                }
            except Exception as error:
                stats["failed"] += 1
                failure = {
                    "document": {"source": {key: record.get(key) for key in (
                        "doc_id", "corp_code", "corp_name", "listed_name", "report_nm", "rcept_no", "rcept_dt",
                        "doc_group", "doc_subtype", "base_year", "base_month", "is_correction", "file_path")},
                        "file_format": record.get("file_format"), "version": {"is_latest_version": True},
                        "parse_summary": {"status": "failed", "warning_count": 0, "error_count": 1}, "record_counts": {}},
                    "sections": [], "text_chunks": [], "logical_tables": [], "table_cells": [],
                    "corrections": [], "events": [], "images": [],
                    "parse_log": {"status": "failed", "files": [], "warnings": [], "errors": [str(error)]},
                }
                store.upsert_result(record, failure, refresh_fts=refresh_fts)
                details = {"time": _now(), "run_id": run_id, "position": position, "total": len(chosen),
                           "doc_id": record["doc_id"], "doc_group": record.get("doc_group"),
                           "status": "failed", "seconds": round(time.monotonic() - item_started, 3), "errors": [str(error)]}
            log.write(json.dumps(details, ensure_ascii=False, separators=(",", ":")) + "\n"); log.flush()
            stats["attempted"] += 1
            if progress_every and stats["attempted"] % progress_every == 0:
                elapsed = time.monotonic() - started
                print(json.dumps({"processed": stats["attempted"], "selected": len(chosen), "stats": dict(stats),
                                  "elapsed_seconds": round(elapsed, 1)}, ensure_ascii=False), flush=True)
    linked = store.finalize_version_links()
    elapsed = time.monotonic() - started
    counts = store.counts()
    final_status = "completed_with_failures" if stats["failed"] else "completed"
    store.conn.execute(
        """UPDATE parse_runs SET finished_at=?,status=?,attempted=?,success=?,warning=?,failed=?,details_json=? WHERE run_id=?""",
        (_now(), final_status, stats["attempted"], stats["success"], stats["warning"], stats["failed"],
         json.dumps({"groups": groups, "limit": limit, "resume": resume, "skipped": stats["skipped"],
                     "elapsed_seconds": elapsed, "version_links_updated": linked, "counts": counts}, ensure_ascii=False), run_id),
    ); store.conn.commit(); store.close()
    return {"run_id": run_id, "status": final_status, "selected": len(chosen), "stats": dict(stats),
            "elapsed_seconds": round(elapsed, 2), "version_links_updated": linked, "database_counts": counts,
            "db_path": str(db_path), "log_path": str(log_path)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the structured disclosure SQLite database")
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--groups", nargs="+", choices=["periodic", "major", "exchange", "holding"],
                    default=["periodic", "major", "exchange", "holding"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--doc-id", action="append", dest="doc_ids", help="Process only this doc_id; repeatable")
    ap.add_argument("--corrections-only", action="store_true")
    ap.add_argument("--preserve-fts", action="store_true", help="Keep existing FTS rows when replacing documents")
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--no-attachments", action="store_true")
    ap.add_argument("--progress-every", type=int, default=25)
    args = ap.parse_args()
    summary = run(args.data_root, args.db, args.groups, args.limit, not args.no_resume,
                  not args.no_attachments, args.progress_every, args.doc_ids, args.corrections_only,
                  not args.preserve_fts)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["stats"].get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
