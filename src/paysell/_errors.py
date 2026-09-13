from __future__ import annotations

from typing import Any


class PaysellError(Exception):
    """Base class for every exception raised by this library."""


class APIConnectionError(PaysellError):
    """The request never got an answer: DNS, TCP, TLS or a dropped connection.

    For ``create_invoice`` this does not mean the invoice was not created —
    the answer may have been lost on the way back. Retry with the same
    ``idempotency_key``.
    """


class APITimeoutError(APIConnectionError):
    """The request did not finish within the configured timeout."""


class APIError(PaysellError):
    """The API answered with a non-2xx status.

    :ivar status_code: HTTP status.
    :ivar code: machine-readable error code, e.g. ``"invalid_input"``. Branch on this.
    :ivar message: human-readable explanation. Log it, don't parse it.
    :ivar details: extra detail strings, when the API sent any.
    :ivar errors: field-level validation errors for a malformed request body.
    :ivar body: the decoded response body (or raw text if it was not JSON).
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: list[str] | None = None,
        errors: list[dict[str, Any]] | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.errors = errors
        self.body = body

    def __str__(self) -> str:
        text = f"[{self.status_code} {self.code}] {self.message}"
        if self.details:
            text += " (" + "; ".join(self.details) + ")"
        return text


class AuthenticationError(APIError):
    """401 — the API key is missing, malformed, unknown or revoked."""


class NotFoundError(APIError):
    """404 — no such invoice, or it belongs to another shop."""


class ConflictError(APIError):
    """409 — the invoice is in a state that forbids this, e.g. cancelling a paid one."""


class InvalidRequestError(APIError):
    """422 — malformed request, or an amount outside the invoice limits."""


class RateLimitError(APIError):
    """429 — too many requests, invoices this hour, or open invoices.

    :ivar retry_after: seconds to wait, from the ``Retry-After`` header.
    """

    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class ServiceUnavailableError(APIError):
    """5xx — the processing core could not be reached or failed.

    For ``create_invoice`` the invoice may still have been created. Retry
    with the same ``idempotency_key``.
    """


class WebhookVerificationError(PaysellError):
    """A webhook failed verification: bad or missing signature, stale timestamp,
    or a body that is not a Paysell event. Answer such a request with 4xx."""


_STATUS_TO_ERROR: dict[int, type[APIError]] = {
    401: AuthenticationError,
    404: NotFoundError,
    409: ConflictError,
    422: InvalidRequestError,
    429: RateLimitError,
}


def error_class_for(status_code: int) -> type[APIError]:
    if status_code in _STATUS_TO_ERROR:
        return _STATUS_TO_ERROR[status_code]
    if status_code >= 500:
        return ServiceUnavailableError
    return APIError
