"""Domain errors raised by services; the API maps them to problem+json by ``status``."""

from typing import Any


class ServiceError(Exception):
    status = 400

    def __init__(self, detail: str, **extensions: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extensions = extensions


class NotFoundError(ServiceError):
    status = 404


class ConflictError(ServiceError):
    status = 409


class InvalidError(ServiceError):
    status = 422
