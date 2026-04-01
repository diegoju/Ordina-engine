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
    raw = os.getenv("MCP_ALLOWED_ORIGINS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _validate_origin(origin: Optional[str]) -> None:
    allowed = _allowed_origins()
    if not allowed or origin is None:
        return
    if origin not in allowed:
        raise HTTPException(status_code=403, detail="Origin no permitido")


def _ensure_session(session_id: Optional[str], initialize_only: bool) -> Optional[str]:
    if initialize_only:
        return None
    if not session_id:
        raise HTTPException(status_code=400, detail="Falta header Mcp-Session-Id")
    if session_id not in ACTIVE_SESSIONS:
        raise HTTPException(status_code=404, detail="Sesion MCP no encontrada")
    return session_id


def _is_request(message: Any) -> bool:
    return isinstance(message, dict) and isinstance(message.get("method"), str)


def _is_initialize_request(message: Any) -> bool:
    return _is_request(message) and message.get("method") == "initialize"


def _normalize_messages(payload: Any) -> tuple[list[dict[str, Any]], bool]:
    if isinstance(payload, list):
        if not all(isinstance(item, dict) for item in payload):
            raise HTTPException(status_code=400, detail="Batch JSON-RPC invalido")
        return payload, True
    if isinstance(payload, dict):
        return [payload], False
    raise HTTPException(status_code=400, detail="Payload JSON-RPC invalido")


def _messages_are_initialize_only(messages: list[dict[str, Any]]) -> bool:
    requests = [message for message in messages if _is_request(message)]
    return bool(requests) and all(_is_initialize_request(message) for message in requests)


def _collect_responses(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    responses: list[dict[str, Any]] = []
    for message in messages:
        response = mcp_server._handle_jsonrpc_message(message)
        if response is not None:
            responses.append(response)
    return responses


def _session_headers(messages: list[dict[str, Any]]) -> dict[str, str]:
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
    _validate_origin(origin)
    return Response(status_code=405)


@app.delete(MCP_PATH)
async def delete_mcp(
    origin: Optional[str] = Header(default=None),
    mcp_session_id: Optional[str] = Header(default=None, alias=SESSION_HEADER),
) -> Response:
    _validate_origin(origin)
    if not mcp_session_id:
        raise HTTPException(status_code=400, detail="Falta header Mcp-Session-Id")
    if mcp_session_id not in ACTIVE_SESSIONS:
        raise HTTPException(status_code=404, detail="Sesion MCP no encontrada")
    ACTIVE_SESSIONS.discard(mcp_session_id)
    return Response(status_code=204)
