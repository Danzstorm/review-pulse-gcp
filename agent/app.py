import logging
import os

from fastapi import FastAPI, HTTPException
from google import genai
from google.cloud import bigquery
from pydantic import BaseModel, Field

import agent

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")

log = logging.getLogger("review-pulse-agent")
app = FastAPI(title="Review Pulse agent")
bq = bigquery.Client(project=PROJECT)
llm = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@app.get("/health")  # not /healthz: Cloud Run reserves that path and answers 404 itself
def health():
    return {"status": "ok"}


@app.post("/ask")
def ask(body: Question):
    try:
        return agent.ask(body.question, bq=bq, client=llm, project=PROJECT)
    except Exception:  # the client gets a stable message; the details stay in the logs
        log.exception("ask failed")
        raise HTTPException(status_code=502, detail="The agent could not answer right now.") from None
