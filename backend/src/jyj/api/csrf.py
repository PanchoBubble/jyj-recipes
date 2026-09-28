from starlette.types import ASGIApp, Receive, Scope, Send

from jyj.api.problems import problem

CSRF_HEADER = b"x-requested-with"
CSRF_VALUE = b"jyj"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class CSRFHeaderMiddleware:
    """Reject state-changing API requests that lack `X-Requested-With: jyj`.

    A cross-site form or simple fetch cannot set a custom header without a CORS preflight,
    which this API never grants, so the header proves the request came from our own UI.
    """

    def __init__(self, app: ASGIApp, prefix: str = "/api/v1") -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] not in SAFE_METHODS
            and (scope["path"] == self.prefix or scope["path"].startswith(self.prefix + "/"))
            and dict(scope["headers"]).get(CSRF_HEADER) != CSRF_VALUE
        ):
            response = problem(403, "Missing or invalid X-Requested-With header")
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
