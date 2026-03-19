from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any

import httpx


class TelegramError(Exception):
    """Base exception for Telegram client errors."""


class TelegramRequestError(TelegramError):
    """Raised when a Telegram request fails after retries."""


class TelegramClient:
    """Minimal synchronous Telegram Bot API client with retry/backoff."""

    def __init__(
        self,
        bot_token: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self._bot_token = bot_token
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._client = httpx.Client(
            base_url="https://api.telegram.org",
            timeout=timeout,
        )

    def __enter__(self) -> "TelegramClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        """Send a text message to a Telegram chat."""
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        response = self.request(
            "POST",
            f"/bot{self._bot_token}/sendMessage",
            json=payload,
        )
        return response.json()

    @contextmanager
    def _suppress_http_request_logs(self):
        """Temporarily suppress request-level logs that can contain tokenized URLs."""
        logger_names = ("httpx", "httpcore")
        loggers = [logging.getLogger(name) for name in logger_names]
        previous_levels = [logger.level for logger in loggers]
        previous_propagates = [logger.propagate for logger in loggers]

        try:
            for logger in loggers:
                logger.setLevel(logging.WARNING)
                logger.propagate = False
            yield
        finally:
            for logger, level, propagate in zip(
                loggers, previous_levels, previous_propagates
            ):
                logger.setLevel(level)
                logger.propagate = propagate

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
    ) -> httpx.Response:
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                with self._suppress_http_request_logs():
                    response = self._client.request(
                        method,
                        path,
                        json=json,
                        timeout=self._timeout,
                    )
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt >= self._max_retries:
                    raise TelegramRequestError(
                        f"Request failed after {self._max_retries} attempts: {exc}"
                    ) from exc
                self._sleep_backoff(attempt)
                continue

            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt >= self._max_retries:
                    raise TelegramRequestError(
                        "Request failed with "
                        f"status {response.status_code} after {self._max_retries} attempts."
                    )
                self._sleep_backoff(attempt)
                continue

            if 400 <= response.status_code <= 499:
                raise TelegramRequestError(
                    "Request failed with "
                    f"status {response.status_code} ({response.reason_phrase})."
                )

            return response

        raise TelegramRequestError("Request failed after retries.") from last_exc

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self._backoff_base * (2 ** (attempt - 1))
        time.sleep(delay)
