"""Local command-line interface for corpus build statistics and QA."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from src.agent.disclosure_agent import DisclosureAgent


def main() -> int:
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask"); ask.add_argument("--db", type=Path, required=True); ask.add_argument("question")
    ask.add_argument("--question-id", default="local"); ask.add_argument("--use-llm", action="store_true")
    stats = sub.add_parser("stats"); stats.add_argument("--db", type=Path, required=True)
    args = ap.parse_args()
    if args.command == "ask":
        agent = DisclosureAgent(args.db)
        try: result = agent.answer(args.question_id, args.question, use_llm=args.use_llm)
        finally: agent.close()
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    tables = ["companies", "documents", "sections", "chunks", "logical_tables", "cells", "corrections", "events"]
    result = {name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in tables}
    result["document_status"] = {row[0]: row[1] for row in conn.execute("SELECT parse_status,count(*) FROM documents GROUP BY parse_status")}
    result["document_groups"] = {row[0]: row[1] for row in conn.execute("SELECT doc_group,count(*) FROM documents GROUP BY doc_group")}
    conn.close(); print(json.dumps(result, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
