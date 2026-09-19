"""Early authentication and real body-byte limits before multipart parsing."""

import secrets
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

from app.core import Settings


def authorized(settings: Settings, supplied: str | None) -> bool:
    expected = settings.api_token.get_secret_value()
    return not expected or secrets.compare_digest((supplied or "").encode(), expected.encode())


class RequestGuard:
    def __init__(self, app, settings: Settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        headers = Headers(scope=scope)
        path = scope["path"]

        async def wrapped_send(message):
            if message["type"] == "http.response.start":
                outgoing = MutableHeaders(scope=message)
                outgoing["X-Request-ID"] = request_id
                outgoing["Cache-Control"] = "no-store"
                outgoing["X-Content-Type-Options"] = "nosniff"
            await send(message)

        async def reject(code, message, status):
            response = JSONResponse({"error": {"code": code, "message": message, "request_id": request_id}}, status_code=status)
            await response(scope, receive, wrapped_send)

        if path.startswith(("/datasets", "/analysis", "/integrations")) and not authorized(self.settings, headers.get("X-API-Key")):
            return await reject("unauthorized", "A valid X-API-Key is required", 401)
        if scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, wrapped_send)
        limit = self.settings.max_upload_bytes + 65536 if path == "/datasets/upload" else 65536
        declared = headers.get("content-length")
        if declared is not None and (len(declared) > 20 or not declared.isascii() or not declared.isdecimal()):
            return await reject("invalid_content_length", "Invalid Content-Length", 400)
        if declared is not None and int(declared) > limit:
            return await reject("body_too_large", "Request body limit exceeded", 413)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > limit:
                return await reject("body_too_large", "Request body limit exceeded", 413)
            if not message.get("more_body", False):
                break
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, wrapped_send)
