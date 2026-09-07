"""FastAPI server exposing the CLAIR-Fin pipeline as a single /api/query endpoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from clairfin.graph.run import run_question
from clairfin.utils.logging_setup import configure_logging

configure_logging("server")
logger = logging.getLogger(__name__)

app = FastAPI(title="CLAIR-Fin API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    question: str


@app.post("/api/query")
def query(request: QueryRequest) -> dict:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")
    try:
        return run_question(question)
    except Exception as exc:  # noqa: BLE001 — surface pipeline failures to the UI instead of a bare 500
        logger.exception("Pipeline run failed for question: %s", question)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
