import logging
import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from google import genai
from google.cloud import bigquery
from pydantic import BaseModel, Field

import agent

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
MAX_TURNS = 10  # earlier messages a client may send: bounds the tokens (and the cost) per request

log = logging.getLogger("review-pulse-agent")
app = FastAPI(title="Review Pulse agent")
bq = bigquery.Client(project=PROJECT)
llm = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)


class Turn(BaseModel):
    role: Literal["user", "model"]
    text: str = Field(min_length=1, max_length=3000)


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    # The service is stateless (it scales to zero and may run on two instances), so the client
    # sends the conversation so far: the "answer" of each reply goes back as a "model" turn.
    history: list[Turn] = Field(default_factory=list, max_length=MAX_TURNS)


@app.get("/health")  # not /healthz: Cloud Run reserves that path and answers 404 itself
def health():
    return {"status": "ok"}


@app.post("/ask")
def ask(body: Question):
    try:
        return agent.ask(body.question, bq=bq, client=llm, project=PROJECT,
                         history=[t.model_dump() for t in body.history])
    except Exception:  # the client gets a stable message; the details stay in the logs
        log.exception("ask failed")
        raise HTTPException(status_code=502, detail="The agent could not answer right now.") from None
