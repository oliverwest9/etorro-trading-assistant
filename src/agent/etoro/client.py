from __future__ import annotations

import time
import uuid
from typing import Any, Mapping

import httpx

from agent.config import Settings


class EToroError(Exception):
    """Base exception for eToro client errors."""


class EToroAuthError(EToroError):
    """Raised when authentication fails."""


class EToroRequestError(EToroError):
    """Raised when a request fails after retries."""


class EToroClient:
    def __init__(
        self,
        settings: Settings,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self._settings = settings
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._client = httpx.Client(base_url=settings.etoro_base_url, timeout=timeout)

    def __enter__(self) -> "EToroClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        return self.request("GET", path, params=params, timeout=timeout)

    def post(
        self,
        path: str,
        *,
        json: Any | None = None,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        return self.request("POST", path, params=params, json=json, timeout=timeout)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._client.request(
                    method,
                    path,
                    headers=self._build_headers(),
                    params=params,
                    json=json,
                    timeout=timeout or self._timeout,
                )
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt >= self._max_retries:
                    raise EToroRequestError(
                        f"Request failed after {self._max_retries} attempts: {exc}"
                    ) from exc
                self._sleep_backoff(attempt)
                continue

            if response.status_code in (401, 403):
                raise EToroAuthError(
                    "Authentication failed with "
                    f"status {response.status_code} ({response.reason_phrase})."
                )

            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt >= self._max_retries:
                    raise EToroRequestError(
                        "Request failed with "
                        f"status {response.status_code} after {self._max_retries} attempts."
                    )
                self._sleep_backoff(attempt)
                continue

            if 400 <= response.status_code <= 499:
                raise EToroRequestError(
                    "Request failed with "
                    f"status {response.status_code} ({response.reason_phrase})."
                )

            return response

        raise EToroRequestError("Request failed after retries.") from last_exc

    def _build_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._settings.etoro_api_key,
            "x-user-key": self._settings.etoro_user_key,
            "x-request-id": str(uuid.uuid4()),
        }

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self._backoff_base * (2 ** (attempt - 1))
        time.sleep(delay)
