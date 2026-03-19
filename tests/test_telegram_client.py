import pytest

from agent.telegram.client import TelegramClient, TelegramRequestError


def test_send_message_success(httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://api.telegram.org/bottest-token/sendMessage",
        status_code=200,
        json={"ok": True, "result": {"message_id": 1}},
    )

    with TelegramClient("test-token") as client:
        response = client.send_message(chat_id="1234", text="hello")

    assert response["ok"] is True
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "POST"


def test_retries_transient_errors(httpx_mock, monkeypatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "agent.telegram.client.time.sleep",
        lambda delay: sleep_calls.append(delay),
    )

    httpx_mock.add_response(
        url="https://api.telegram.org/bottest-token/sendMessage",
        status_code=500,
    )
    httpx_mock.add_response(
        url="https://api.telegram.org/bottest-token/sendMessage",
        status_code=502,
    )
    httpx_mock.add_response(
        url="https://api.telegram.org/bottest-token/sendMessage",
        status_code=200,
        json={"ok": True, "result": {"message_id": 2}},
    )

    with TelegramClient("test-token", backoff_base=0.01) as client:
        response = client.send_message(chat_id="1234", text="hello")

    assert response["ok"] is True
    assert len(httpx_mock.get_requests()) == 3
    assert sleep_calls == [0.01, 0.02]


def test_raises_on_non_retryable_4xx(httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://api.telegram.org/bottest-token/sendMessage",
        status_code=400,
    )

    with TelegramClient("test-token") as client:
        with pytest.raises(TelegramRequestError) as excinfo:
            client.send_message(chat_id="bad", text="hello")

    assert "400" in str(excinfo.value)
    assert len(httpx_mock.get_requests()) == 1


def test_raises_after_retry_exhaustion(httpx_mock, monkeypatch) -> None:
    monkeypatch.setattr("agent.telegram.client.time.sleep", lambda _: None)

    httpx_mock.add_response(
        url="https://api.telegram.org/bottest-token/sendMessage",
        status_code=503,
    )
    httpx_mock.add_response(
        url="https://api.telegram.org/bottest-token/sendMessage",
        status_code=503,
    )
    httpx_mock.add_response(
        url="https://api.telegram.org/bottest-token/sendMessage",
        status_code=503,
    )

    with TelegramClient("test-token", backoff_base=0.01) as client:
        with pytest.raises(TelegramRequestError) as excinfo:
            client.send_message(chat_id="1234", text="hello")

    assert "503" in str(excinfo.value)
    assert len(httpx_mock.get_requests()) == 3
