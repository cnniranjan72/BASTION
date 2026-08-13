"""Consistent error envelope across every endpoint — API_SPEC.md §Error format."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: UUID


class ErrorResponse(BaseModel):
    error: ErrorDetail


class BastionError(Exception):
    def __init__(self, code: str, message: str, request_id: UUID, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id
        self.status_code = status_code

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(
            error=ErrorDetail(code=self.code, message=self.message, request_id=self.request_id)
        )
