"""Endpoint tests for the loan agent server."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient


def test_health():
    from loan_agent.server import app
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_run_returns_fd_agent_response():
    from loan_agent.server import app
    client = TestClient(app)

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"response": "Customer C001 has 2 FDs totaling INR 350,000"}

    with patch("loan_agent.server.token_provider") as mock_provider, \
         patch("loan_agent.server.httpx.AsyncClient") as mock_client_class:

        async def fake_get_token():
            return "test-token"

        mock_provider.get_token = fake_get_token

        mock_async_client = AsyncMock()
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=None)
        mock_async_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_async_client

        resp = client.post("/run", json={"query": "How many FDs does C001 have?"})
        assert resp.status_code == 200
        assert "response" in resp.json()
