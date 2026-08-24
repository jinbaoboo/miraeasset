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
from src.domain.metric_ontology import public_metric_definitions


DB_PATH = Path(os.getenv("DISCLOSURE_DB", "outputs/disclosures.db"))
app = FastAPI(title="Mirae Asset Disclosure Agent", version="0.2.0",
              description="구조화 공시 근거, 계산식, 검증 결과를 함께 반환하는 사전 HyperCLOVA 단계 API")


@app.get("/health")
def health():
    return {"status": "ok" if DB_PATH.is_file() else "not_ready", "database": str(DB_PATH)}


@app.get("/metrics", summary="지원 재무·공시 지표 정의")
def metrics():
    definitions = public_metric_definitions()
    return {"count": len(definitions), "metrics": definitions}


@app.get("/answer")
def answer(question_id: str = Query(..., min_length=1, max_length=200),
           question: str = Query(..., min_length=1, max_length=4000), use_llm: bool = Query(True),
           debug: bool = Query(True, description="false면 검색 원문과 실행 trace를 숨깁니다.")):
    if not DB_PATH.is_file():
        raise HTTPException(status_code=503, detail=f"Structured database not found: {DB_PATH}")
    agent = DisclosureAgent(DB_PATH)
    try:
        result = agent.answer(question_id, question, use_llm=use_llm)
        if not debug:
            result.pop("retrieved_context", None)
            result.pop("think_trace", None)
        return result
    finally:
        agent.close()
