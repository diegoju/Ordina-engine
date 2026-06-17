"""HTTP transport wrapper for the Ordina-engine MCP server.

This module exposes the MCP protocol over HTTP/JSON-RPC so that clients that
cannot use stdio (e.g. web apps, remote agents) can still communicate with
the MCP server.

Protocol overview
-----------------
- POST /mcp   — send one JSON-RPC message or a batch list of messages.
                On the very first request the payload must contain an
                ``initialize`` request; the server responds with a new session
                ID in the ``Mcp-Session-Id`` response header.  Every subsequent
                request must include that header.
- GET  /mcp   — returns 405 (not supported; SSE streaming not implemented).
- DELETE /mcp — terminate an active session.

Origin restriction
------------------
Set the ``MCP_ALLOWED_ORIGINS`` environment variable to a comma-separated list
of allowed origins.  Leave it empty (the default) to allow all origins.

Session lifecycle
-----------------
Sessions are kept in an in-process set (``ACTIVE_SESSIONS``).  They persist
for the lifetime of the process; there is currently no automatic expiry.
"""
from __future__ import annotations

import os
import secrets
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

import mcp_server


SESSION_HEADER = "Mcp-Session-Id"
MCP_PATH = "/mcp"
ACTIVE_SESSIONS: set[str] = set()

app = FastAPI(title="Ordina-engine MCP HTTP", version=mcp_server.VERSION)


def _allowed_origins() -> set[str]:
    """Return the set of origins permitted by MCP_ALLOWED_ORIGINS env var.

    Returns an empty set when the variable is unset, which means all origins
    are allowed (open — suitable for local development only).
    """
    raw = os.getenv("MCP_ALLOWED_ORIGINS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _validate_origin(origin: Optional[str]) -> None:
    """Raise HTTP 403 if the request Origin is not in the allow-list.

    Does nothing when the allow-list is empty (unrestricted access).
    """
    allowed = _allowed_origins()
    if not allowed or origin is None:
        return
    if origin not in allowed:
        raise HTTPException(status_code=403, detail="Origin no permitido")


def _ensure_session(session_id: Optional[str], initialize_only: bool) -> Optional[str]:
    """Validate that a non-initialize request carries a known session ID.

    Returns ``None`` for initialize-only batches (no session required yet).
    Raises HTTP 400 if the header is missing, HTTP 404 if the session is unknown.
    """
    if initialize_only:
        return None
    if not session_id:
        raise HTTPException(status_code=400, detail="Falta header Mcp-Session-Id")
    if session_id not in ACTIVE_SESSIONS:
        raise HTTPException(status_code=404, detail="Sesion MCP no encontrada")
    return session_id


def _is_request(message: Any) -> bool:
    """Return True if *message* looks like a JSON-RPC request (has a method string)."""
    return isinstance(message, dict) and isinstance(message.get("method"), str)


def _is_initialize_request(message: Any) -> bool:
    """Return True if *message* is an MCP ``initialize`` request."""
    return _is_request(message) and message.get("method") == "initialize"


def _normalize_messages(payload: Any) -> tuple[list[dict[str, Any]], bool]:
    """Coerce *payload* into a list of JSON-RPC messages and a batch flag.

    Returns ``(messages, is_batch)`` where *is_batch* is True when the client
    sent an array.  Raises HTTP 400 for invalid shapes.
    """
    if isinstance(payload, list):
        if not all(isinstance(item, dict) for item in payload):
            raise HTTPException(status_code=400, detail="Batch JSON-RPC invalido")
        return payload, True
    if isinstance(payload, dict):
        return [payload], False
    raise HTTPException(status_code=400, detail="Payload JSON-RPC invalido")


def _messages_are_initialize_only(messages: list[dict[str, Any]]) -> bool:
    """Return True when every JSON-RPC request in *messages* is an initialize call."""
    requests = [message for message in messages if _is_request(message)]
    return bool(requests) and all(_is_initialize_request(message) for message in requests)


def _collect_responses(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dispatch each message through the MCP server and collect non-None responses."""
    responses: list[dict[str, Any]] = []
    for message in messages:
        response = mcp_server._handle_jsonrpc_message(message)
        if response is not None:
            responses.append(response)
    return responses


def _session_headers(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Create a new session for initialize-only batches and return the session header.

    Returns an empty dict for any other batch (session already established).
    """
    if not _messages_are_initialize_only(messages):
        return {}
    session_id = secrets.token_urlsafe(24)
    ACTIVE_SESSIONS.add(session_id)
    return {SESSION_HEADER: session_id}


@app.post(MCP_PATH)
async def post_mcp(
    request: Request,
    response: Response,
    origin: Optional[str] = Header(default=None),
    mcp_session_id: Optional[str] = Header(default=None, alias=SESSION_HEADER),
) -> Response:
    """Handle a JSON-RPC message or batch.

    - First call must be an ``initialize`` request (no session header needed).
    - Subsequent calls must include the ``Mcp-Session-Id`` header returned by
      the initialize response.
    - Returns HTTP 202 when all messages are notifications (no response body).
    - Returns a JSON-RPC response object or array otherwise.
    """
    _validate_origin(origin)

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON invalido") from exc

    messages, is_batch = _normalize_messages(payload)
    initialize_only = _messages_are_initialize_only(messages)
    _ensure_session(mcp_session_id, initialize_only)

    session_headers = _session_headers(messages)
    for key, value in session_headers.items():
        response.headers[key] = value

    responses = _collect_responses(messages)
    if not responses:
        return Response(status_code=202, headers=dict(response.headers))

    body: Any = responses if is_batch else responses[0]
    return JSONResponse(content=body, headers=dict(response.headers))


@app.get(MCP_PATH)
async def get_mcp(origin: Optional[str] = Header(default=None)) -> Response:
    """Return 405 — GET is not supported (SSE streaming not implemented)."""
    _validate_origin(origin)
    return Response(status_code=405)


@app.delete(MCP_PATH)
async def delete_mcp(
    origin: Optional[str] = Header(default=None),
    mcp_session_id: Optional[str] = Header(default=None, alias=SESSION_HEADER),
) -> Response:
    """Terminate an active MCP session.

    Removes the session from ``ACTIVE_SESSIONS``.  Returns HTTP 204 on success.
    """
    _validate_origin(origin)
    if not mcp_session_id:
        raise HTTPException(status_code=400, detail="Falta header Mcp-Session-Id")
    if mcp_session_id not in ACTIVE_SESSIONS:
        raise HTTPException(status_code=404, detail="Sesion MCP no encontrada")
    ACTIVE_SESSIONS.discard(mcp_session_id)
    return Response(status_code=204)
