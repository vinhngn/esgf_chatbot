from __future__ import annotations

from typing import Any

import requests


class ApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "",
        retryable: bool = False,
        connection_failure: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retryable = retryable
        self.connection_failure = connection_failure


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 180,
) -> dict[str, Any]:
    try:
        response = requests.request(
            method,
            f"{base_url.rstrip('/')}{path}",
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise ApiError(
            "The T2C API is unavailable.",
            connection_failure=True,
            retryable=True,
        ) from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise ApiError(
            f"API returned a non-JSON response ({response.status_code}).",
            status_code=response.status_code,
        ) from exc
    if not response.ok:
        raise ApiError(
            str(body.get("error") or body.get("detail") or body),
            status_code=response.status_code,
            code=str(body.get("error_code") or ""),
            retryable=bool(body.get("retryable")),
        )
    return body
