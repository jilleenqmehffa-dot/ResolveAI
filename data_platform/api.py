"""Internal read-only catalog and benchmark run API."""

from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException

from data_platform.core import catalog, manifest
from data_platform.ingestions import list_ingestions
from data_platform.runs import get_run, list_runs

app = FastAPI(title="ResolveAI Data Platform", version="1.0")


def require_token(x_agent_token: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("AGENT_INTERNAL_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="internal token is not configured")
    if not x_agent_token or not secrets.compare_digest(x_agent_token, expected):
        raise HTTPException(status_code=401, detail="invalid internal token")


AUTH = Depends(require_token)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/datasets", dependencies=[AUTH])
def datasets() -> dict:
    pinned = manifest()
    return {"version": pinned["version"], "sources": catalog(), "checksums": pinned["sources"]}


@app.get("/runs", dependencies=[AUTH])
def runs() -> list[dict]:
    return list_runs()


@app.get("/ingestions", dependencies=[AUTH])
def ingestions() -> list[dict]:
    return list_ingestions()


@app.get("/runs/compare", dependencies=[AUTH])
def compare_runs(left: str, right: str) -> dict:
    first, second = get_run(left), get_run(right)
    if first is None or second is None:
        raise HTTPException(status_code=404, detail="run not found")
    first_scores = first["summary"]["mean_scores"]
    second_scores = second["summary"]["mean_scores"]
    return {
        "left": left, "right": right,
        "same_dataset": (
            first["sources"] == second["sources"]
            and first["task_ids"] == second["task_ids"]
            and first["split"] == second["split"]
        ),
        "both_isolated": first["rebuild_each_task"] and second["rebuild_each_task"],
        "score_delta": {
            name: second_scores[name] - first_scores[name]
            for name in first_scores.keys() & second_scores.keys()
        },
        "left_summary": first["summary"], "right_summary": second["summary"],
    }


@app.get("/runs/{run_id}", dependencies=[AUTH])
def run(run_id: str) -> dict:
    entry = get_run(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="run not found")
    return entry
