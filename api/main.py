"""
Panelist — FastAPI backend.

Endpoints (minimum):
    POST /generate        -> trigger dataset generation (or load pre-generated data)
    POST /schedule         -> run initial scheduler, return schedule + metrics
    GET  /schedule         -> current schedule state
    POST /replan           -> apply a disruption, return diff for coordinator confirm/reject
    POST /replan/apply      -> commit a previously-returned replan diff
    GET  /metrics           -> current schedule metrics
"""

from fastapi import FastAPI

app = FastAPI(title="Panelist API")


@app.get("/health")
def health():
    return {"status": "ok"}


# TODO: /generate, /schedule, /replan, /replan/apply, /metrics
