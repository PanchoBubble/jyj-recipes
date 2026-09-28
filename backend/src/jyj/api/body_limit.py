from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jyj.api.problems import problem

TOO_LARGE = "Request body is too large"


class BodySizeLimitMiddleware:
    """Cap every request body: refuse a large Content-Length up front, count streamed bytes."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_large = int(declared) > self.max_bytes
            except ValueError:
                too_large = False
            if too_large:
                await problem(413, TOO_LARGE)(scope, receive, send)
                return

        received = 0

        async def capped_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(413, TOO_LARGE)
            return message

        await self.app(scope, capped_receive, send)
