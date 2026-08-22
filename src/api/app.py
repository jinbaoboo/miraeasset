"""Contest-compatible API.

Run with: ``uvicorn src.api.app:app --host 0.0.0.0 --port 8000``.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException, Query
except ImportError as error:
    raise RuntimeError("FastAPI is not installed. Run: pip install -r requirements.txt") from error

from src.agent.disclosure_agent import DisclosureAgent


DB_PATH = Path(os.getenv("DISCLOSURE_DB", "outputs/disclosures.db"))
app = FastAPI(title="Mirae Asset Disclosure Agent", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok" if DB_PATH.is_file() else "not_ready", "database": str(DB_PATH)}


@app.get("/answer")
def answer(question_id: str = Query(..., min_length=1, max_length=200),
           question: str = Query(..., min_length=1, max_length=4000), use_llm: bool = Query(True)):
    if not DB_PATH.is_file():
        raise HTTPException(status_code=503, detail=f"Structured database not found: {DB_PATH}")
    agent = DisclosureAgent(DB_PATH)
    try:
        return agent.answer(question_id, question, use_llm=use_llm)
    finally:
        agent.close()
