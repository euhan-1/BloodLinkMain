"""Upload size limit and file-type check for the CSV upload endpoints.

Two layers, because a size check inside the handler comes too late: by the time a handler runs, Starlette has already received the
whole multipart body and written it to a temporary file.

  UploadSizeLimitMiddleware  runs first, on the upload paths only. It refuses a request whose Content-Length is over the limit before
                             reading any of the body, and when there is no Content-Length (chunked) it stops reading as soon as the
                             running total passes the limit. Either way the answer is 413 with a plain message.
  read_csv_upload            the handler's own read: at most the limit (it reads one chunk at a time and stops one byte past the
                             limit, so an oversize file is never loaded), the .csv extension, and a look at the bytes themselves.

The limit is MAX_UPLOAD_BYTES (default 5 MB), read on every request so changing it needs no code change. The donor upload is NOT in
the middleware's path list: it is left exactly as it was.
"""
import os
from typing import Iterable, Optional

from fastapi import HTTPException

DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# A multipart body is the file plus boundary lines and part headers (a few hundred bytes). The middleware, which sees the whole body,
# allows this much on top of the file limit; the exact file limit is then enforced by read_csv_upload.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
_READ_CHUNK = 64 * 1024

# The first bytes of common non-text files people rename to .csv (a spreadsheet saved as .xlsx is a zip, an old .xls is OLE).
_BINARY_SIGNATURES = (b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"%PDF", b"MZ", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"\x1f\x8b", b"Rar!")


def max_upload_bytes() -> int:
    try:
        value = int(os.environ.get("MAX_UPLOAD_BYTES", "").strip() or DEFAULT_MAX_UPLOAD_BYTES)
    except ValueError:
        return DEFAULT_MAX_UPLOAD_BYTES
    return value if value > 0 else DEFAULT_MAX_UPLOAD_BYTES


def _megabytes(n: int) -> str:
    mb = n / (1024 * 1024)
    return f"{mb:.0f} MB" if mb >= 1 and mb == int(mb) else (f"{mb:.1f} MB" if mb >= 1 else f"{max(1, n // 1024)} KB")


def too_large_message(limit: int) -> str:
    return f"That file is too large. Files can be up to {_megabytes(limit)}; split it into smaller files and upload them one at a time."


def looks_binary(head: bytes) -> bool:
    return head.startswith(_BINARY_SIGNATURES) or b"\x00" in head


def read_csv_upload(file, limit: Optional[int] = None) -> str:
    """The text of an uploaded CSV, or an HTTPException: 400 for the wrong kind of file, 413 for too big. `file` is a Starlette
    UploadFile; `file.file` is its synchronous file object (these handlers are plain `def`)."""
    limit = limit or max_upload_bytes()
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="only .csv files are accepted")
    chunks, total = [], 0
    while True:
        chunk = file.file.read(min(_READ_CHUNK, limit + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail=too_large_message(limit))
        chunks.append(chunk)
    raw = b"".join(chunks)
    if looks_binary(raw[:8192]):
        raise HTTPException(status_code=400, detail="that file is not a plain-text CSV (it looks like a spreadsheet or another binary format); save it as CSV and try again")
    try:
        return raw.decode("utf-8-sig")  # utf-8-sig tolerates a BOM from Excel exports
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="file must be UTF-8 encoded")


class _TooLarge(Exception):
    pass


class UploadSizeLimitMiddleware:
    """Pure ASGI. Refuses an oversize upload with 413 before (Content-Length) or while (running total) its body is read."""

    def __init__(self, app, paths: Iterable[str]):
        self.app = app
        self.paths = frozenset(paths)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] not in self.paths:
            await self.app(scope, receive, send)
            return
        file_limit = max_upload_bytes()
        limit = file_limit + MULTIPART_OVERHEAD_BYTES
        state = {"over": False, "answered": False, "seen": 0}

        async def answer():
            if state["answered"]:
                return
            state["answered"] = True
            body = ('{"detail": "%s"}' % too_large_message(file_limit)).encode("utf-8")
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

        declared = next((v for k, v in scope.get("headers", []) if k == b"content-length"), None)
        try:
            if declared is not None and int(declared) > limit:
                await answer()
                return
        except ValueError:
            pass  # a malformed Content-Length is the server's problem to reject, not ours

        async def counting_receive():
            message = await receive()
            if message["type"] == "http.request":
                state["seen"] += len(message.get("body", b""))
                if state["seen"] > limit:
                    state["over"] = True
                    raise _TooLarge()
            return message

        async def send_unless_over(message):
            if state["over"]:
                # The app is already failing on the body we cut off (FastAPI turns that into its own 400): say 413 instead.
                if message["type"] == "http.response.start":
                    await answer()
                return
            await send(message)

        try:
            await self.app(scope, counting_receive, send_unless_over)
        except _TooLarge:
            pass
        if state["over"]:
            await answer()
