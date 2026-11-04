"""PayPal errors with friendly, secret-free messages."""
from __future__ import annotations


class PayPalError(Exception):
    """Base class. `user_message` is safe to show in the UI; it never contains credentials."""

    def __init__(self, user_message: str, *, status: int | None = None, name: str | None = None,
                 debug_id: str | None = None, details: list | None = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.status = status
        self.name = name
        self.debug_id = debug_id
        self.details = details or []


class PayPalNotConfigured(PayPalError):
    pass


class PayPalAuthError(PayPalError):
    """401: PayPal did not accept our credentials or token."""


class PayPalPermissionError(PayPalError):
    """403: authenticated, but not allowed. `missing_scope` is set when we can name it."""

    def __init__(self, user_message: str, *, missing_scope: str | None = None, **kw):
        super().__init__(user_message, **kw)
        self.missing_scope = missing_scope


class PayPalNotFound(PayPalError):
    pass


class PayPalRateLimited(PayPalError):
    pass


class PayPalUnavailable(PayPalError):
    """5xx or network trouble that persisted through every retry."""


class ConfirmationRequired(PayPalError):
    """A state-changing call was attempted without explicit confirmation."""

    def __init__(self, action: str):
        super().__init__(f"This would change something in PayPal ({action}). Nothing was sent: confirmation is required.")
        self.action = action
