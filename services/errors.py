"""Application-level errors. Each carries an HTTP status and a stable error code.

Routes never build error responses by hand: they raise one of these and the
handler in app.py turns it into {"error": {"code", "message", "details"}}.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return body


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class ValidationFailedError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"


class DatabaseNotConfiguredError(ServiceUnavailableError):
    code = "DB_NOT_CONFIGURED"


class DatabaseOperationError(AppError):
    status_code = 500
    code = "DATABASE_ERROR"


class ConstraintViolationError(AppError):
    """A database constraint rejected the change. `constraint` is the Oracle constraint name."""

    status_code = 422
    code = "CONSTRAINT_VIOLATION"

    def __init__(self, message: str, *, constraint: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.constraint = constraint


class ResourceBusyError(ConflictError):
    code = "RESOURCE_BUSY"
