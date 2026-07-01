"""Workload identity token acquisition for the loan agent.

Three operating modes controlled by environment variables:

agentgateway STS OBO exchange (USE_AGENTGATEWAY_STS=true)
  Two-step RFC 8693 exchange used in UC1 (fd-loan-rbac-native-obo):
  1. Fetch a Keycloak access token via client_credentials (client_id=loan-agent).
  2. POST KC token + K8s SA JWT to the agentgateway STS (STS_URL).
  The STS returns a short-lived OBO token (iss=STS, client_id=loan-agent) that is
  accepted at /fd-agent (Strict JWT, STS issuer).

KC token-exchange (USE_KEYCLOAK_EXCHANGE=true, USE_AGENTGATEWAY_STS=false)
  Exchange the auto-mounted K8s SA JWT directly at Keycloak (RFC 8693).
  Used in UC2 (workload-identity chain).

Client credentials (default)
  Plain Keycloak client_credentials token — no STS or SA exchange.

Token cached in-memory, refreshed 30 seconds before expiry.
"""

import asyncio
import logging
import os
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://keycloak.keycloak.svc.cluster.local:8080")
_REALM = os.environ.get("KEYCLOAK_REALM", "agw-dev")
_CLIENT_ID = os.environ.get("CLIENT_ID", "loan-agent")
_CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
_AUDIENCE = os.environ.get("AUDIENCE", "agentgateway")
_USE_KEYCLOAK_EXCHANGE = os.environ.get("USE_KEYCLOAK_EXCHANGE", "false").lower() == "true"
_SA_TOKEN_PATH = os.environ.get("SA_TOKEN_PATH", "/var/run/secrets/tokens/sa-token")
_STS_URL = os.environ.get("STS_URL", "")
_USE_AGENTGATEWAY_STS = os.environ.get("USE_AGENTGATEWAY_STS", "false").lower() == "true"

_GRANT_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
_GRANT_CLIENT_CREDENTIALS = "client_credentials"
_TOKEN_TYPE_JWT = "urn:ietf:params:oauth:token-type:jwt"
_TOKEN_TYPE_ACCESS = "urn:ietf:params:oauth:token-type:access_token"


class WorkloadTokenProvider:
    """Thread-safe async token provider with expiry-aware caching."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._expires_at - 30:
                return self._token
            self._token, self._expires_at = await self._fetch()
            if _USE_AGENTGATEWAY_STS:
                mode = "sts-obo-exchange"
            elif _USE_KEYCLOAK_EXCHANGE:
                mode = "kc-token-exchange"
            else:
                mode = "client_credentials"
            logger.info(
                "Obtained workload identity token via %s (expires in ~%ds)",
                mode,
                int(self._expires_at - time.monotonic()),
            )
            return self._token

    async def _fetch(self) -> tuple[str, float]:
        if _USE_AGENTGATEWAY_STS:
            return await self._fetch_sts_obo()
        token_url = f"{_KEYCLOAK_URL}/realms/{_REALM}/protocol/openid-connect/token"
        data = self._build_exchange_data() if _USE_KEYCLOAK_EXCHANGE else self._build_client_credentials_data()
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(token_url, data=data)
            resp.raise_for_status()
        payload = resp.json()
        return payload["access_token"], time.monotonic() + int(payload.get("expires_in", 300))

    async def _fetch_sts_obo(self) -> tuple[str, float]:
        """Two-step RFC 8693 OBO exchange via the agentgateway STS (UC1).

        Step 1: Keycloak client_credentials → KC access token (client_id=loan-agent).
        Step 2: POST KC token + SA JWT to STS → OBO token (iss=STS, client_id=loan-agent).
        """
        kc_token = await self._fetch_kc_client_credentials()
        sa_token = Path(_SA_TOKEN_PATH).read_text().strip()
        sts_data = {
            "grant_type": _GRANT_TOKEN_EXCHANGE,
            "subject_token": kc_token,
            "subject_token_type": _TOKEN_TYPE_JWT,
            "actor_token": sa_token,
            "actor_token_type": _TOKEN_TYPE_JWT,
        }
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(_STS_URL, data=sts_data)
            resp.raise_for_status()
        payload = resp.json()
        access_token = payload.get("access_token") or payload.get("token")
        expires_in = int(payload.get("expires_in", 3600))
        return access_token, time.monotonic() + expires_in

    async def _fetch_kc_client_credentials(self) -> str:
        token_url = f"{_KEYCLOAK_URL}/realms/{_REALM}/protocol/openid-connect/token"
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(token_url, data=self._build_client_credentials_data())
            resp.raise_for_status()
        return resp.json()["access_token"]

    def _build_client_credentials_data(self) -> dict:
        return {
            "grant_type": _GRANT_CLIENT_CREDENTIALS,
            "client_id": _CLIENT_ID,
            "client_secret": _CLIENT_SECRET,
        }

    def _build_exchange_data(self) -> dict:
        sa_token_path = Path(_SA_TOKEN_PATH)
        if not sa_token_path.exists():
            raise FileNotFoundError(
                f"SA token not found at {_SA_TOKEN_PATH}. "
                "Ensure the deployment has a projected ServiceAccountToken volume."
            )
        sa_token = sa_token_path.read_text().strip()
        data: dict = {
            "grant_type": _GRANT_TOKEN_EXCHANGE,
            "client_id": _CLIENT_ID,
            "subject_token": sa_token,
            "subject_token_type": _TOKEN_TYPE_JWT,
            "requested_token_type": _TOKEN_TYPE_ACCESS,
            "audience": _AUDIENCE,
        }
        if _CLIENT_SECRET:
            data["client_secret"] = _CLIENT_SECRET
        return data


token_provider = WorkloadTokenProvider()
