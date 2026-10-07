# llm/errors.py — what an adapter raises, so callers can decide per kind (retry, back off, give up) without
# knowing the provider.
from __future__ import annotations


class LLMError(Exception):
    """Base class for every failure of a model call."""


class LLMAuthError(LLMError):
    """The key was rejected or lacks access (HTTP 401/403). Retrying will not help."""


class LLMBadRequest(LLMError):
    """The request itself is wrong (HTTP 400/404/422), or asks for something the model profile does not support."""


class LLMRateLimited(LLMError):
    """Too many requests (HTTP 429). `retry_after` is the provider's hint in seconds, if it gave one."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMServerError(LLMError):
    """The provider failed (HTTP 5xx). Usually worth retrying later."""


class LLMTimeout(LLMError):
    """No answer within the timeout."""


class LLMUnavailable(LLMError):
    """The provider could not be reached at all (network down, DNS, refused)."""


class LLMResponseError(LLMError):
    """The provider answered, but not in a shape we can use."""
