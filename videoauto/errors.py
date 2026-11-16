"""Exceptions. Anything a user can hit is a UserError carrying a Vietnamese message."""

from __future__ import annotations


class UserError(Exception):
    """A failure the user can act on. `message` is shown as-is (Vietnamese), no traceback."""

    exit_code = 2

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ToolMissing(UserError):
    pass


class UnreadableFile(UserError):
    pass


class ProviderUnavailable(UserError):
    """No API access: CLI missing, not logged in, bad key, endpoint unreachable."""


class LimitReached(UserError):
    """Usage / rate limit. The run must stop cleanly."""

    exit_code = 3

    def __init__(self, message: str, retry_hint: str | None = None):
        super().__init__(message)
        self.retry_hint = retry_hint


class IndexValidationError(Exception):
    """An index failed schema validation. This is a bug in videoauto, never a warning."""

    exit_code = 4


class PlanValidationError(IndexValidationError):
    """An EditPlan we produced failed its schema. A bug in videoauto, never a warning."""


class BuildAborted(UserError):
    """Too many occ commands failed (or a step every later step depends on failed)."""

    exit_code = 6
