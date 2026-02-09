import uuid

import pytest

from agent.config import Settings
from agent.etoro.client import EToroAuthError, EToroClient, EToroRequestError


def _settings() -> Settings:
    return Settings(
        etoro_api_key="test-api-key",
        etoro_user_key="test-user-key",
        etoro_base_url="https://example.com",
        surreal_url="ws://localhost:8000/rpc",
        surreal_namespace="trading",
        surreal_database="agent",
        surreal_user="root",
        surreal_pass="root",
        llm_provider="openai",
        llm_api_key="test-llm",
        llm_model="gpt-4o",
    )


def test_client_sets_auth_headers_and_unique_request_id(httpx_mock):
    settings = _settings()
    with EToroClient(settings) as client:
        for _ in range(2):
            httpx_mock.add_response(
                url="https://example.com/ping", status_code=200, json={"ok": True}
            )

        for _ in range(2):
            client.get("/ping")

    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    assert requests[0].headers["x-api-key"] == "test-api-key"
    assert requests[0].headers["x-user-key"] == "test-user-key"

    first_id = requests[0].headers["x-request-id"]
    second_id = requests[1].headers["x-request-id"]
    assert first_id != second_id
    uuid.UUID(first_id, version=4)
    uuid.UUID(second_id, version=4)


def test_client_raises_auth_error_on_401(httpx_mock):
    settings = _settings()
    with EToroClient(settings) as client:
        httpx_mock.add_response(url="https://example.com/secure", status_code=401)

        with pytest.raises(EToroAuthError) as excinfo:
            client.get("/secure")

    assert "401" in str(excinfo.value)
    assert len(httpx_mock.get_requests()) == 1


def test_client_raises_auth_error_on_403(httpx_mock):
    settings = _settings()
    with EToroClient(settings) as client:
        httpx_mock.add_response(url="https://example.com/secure", status_code=403)

        with pytest.raises(EToroAuthError) as excinfo:
            client.get("/secure")

    assert "403" in str(excinfo.value)
    assert len(httpx_mock.get_requests()) == 1


def test_client_retries_transient_errors(httpx_mock, monkeypatch):
    settings = _settings()
    sleep_calls: list[float] = []

    monkeypatch.setattr(
        "agent.etoro.client.time.sleep",
        lambda delay: sleep_calls.append(delay),
    )

    with EToroClient(settings, backoff_base=0.01) as client:
        httpx_mock.add_response(url="https://example.com/unstable", status_code=500)
        httpx_mock.add_response(url="https://example.com/unstable", status_code=502)
        httpx_mock.add_response(
            url="https://example.com/unstable", status_code=200, json={"ok": True}
        )

        response = client.get("/unstable")

    assert response.status_code == 200
    assert len(httpx_mock.get_requests()) == 3
    assert sleep_calls == [0.01, 0.02]


def test_client_raises_request_error_on_404(httpx_mock):
    settings = _settings()
    with EToroClient(settings) as client:
        httpx_mock.add_response(url="https://example.com/missing", status_code=404)

        with pytest.raises(EToroRequestError) as excinfo:
            client.get("/missing")

    assert "404" in str(excinfo.value)
