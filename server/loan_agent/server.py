"""FastAPI server for the loan agent.

Endpoint: POST /run
  Body:    {"query": "<natural language question>"}
  No Authorization header required — the agent self-authenticates using a
  Keycloak workload identity token.

Endpoint: GET /health
  Returns: {"status": "ok"}

Flow:
  1. Fetch workload identity token from Keycloak (client_credentials or SA exchange).
  2. Call FD Agent through agentgateway with Authorization: Bearer <token>.
  3. Return the FD Agent's response.
"""

import logging
import os

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .auth import token_provider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("loan_agent.server")

_FD_AGENT_URL = os.environ.get(
    "FD_AGENT_URL",
    "http://agentgateway.agentgateway-system.svc.cluster.local:8080/fd-agent/run",
)

app = FastAPI(title="Loan Agent", version="1.0.0")


class RunRequest(BaseModel):
    query: str


@app.post("/run")
async def run(body: RunRequest):
    try:
        token = await token_provider.get_token()

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                _FD_AGENT_URL,
                json={"query": body.query},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()

        return resp.json()
    except httpx.HTTPStatusError as exc:
        body_text = exc.response.text
        logger.error(
            "FD Agent returned %d: %s",
            exc.response.status_code,
            body_text[:1000],
        )
        return JSONResponse(
            status_code=exc.response.status_code,
            content={"detail": body_text, "type": "HTTPStatusError"},
        )
    except Exception as exc:
        logger.exception("Loan agent run failed")
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "type": type(exc).__name__},
        )


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
