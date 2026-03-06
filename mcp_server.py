from __future__ import annotations

import json
import sys
from typing import Any, Callable, Optional

from fastapi.responses import JSONResponse

import api as ordina_api


def _unwrap_fastapi_response(result: Any) -> Any:
    if not isinstance(result, JSONResponse):
        return result

    raw = result.body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"rawText": raw}

    if result.status_code >= 400:
        return {
            "error": "Upstream request failed",
            "status": result.status_code,
            "upstream": payload,
        }
    return payload


def buscar_ley(id: Optional[int] = None, categoria: Optional[int] = None, nombre: Optional[str] = None) -> Any:
    result = ordina_api.buscar_ley(id=id, categoria=categoria, nombre=nombre)
    return _unwrap_fastapi_response(result)


def buscar_jurisprudencia(q: str = "", page: int = 0, size: int = 10, includeRaw: bool = False) -> Any:
    result = ordina_api.sjf_search(q=q, page=page, size=size, includeRaw=includeRaw)
    return _unwrap_fastapi_response(result)


def obtener_detalle_jurisprudencia(
    ius: int,
    isSemanal: Optional[bool] = None,
    includeRaw: bool = False,
) -> Any:
    result = ordina_api.sjf_detail(
        ius=ius,
        isSemanal=isSemanal,
        hostName="https://sjf2.scjn.gob.mx",
        includeRaw=includeRaw,
    )
    return _unwrap_fastapi_response(result)


def buscar_articulos_jurislex(
    categoria: int,
    idLegislacion: int,
    desc: str = "",
    soloArticulo: bool = False,
    indice: int = 0,
    elementos: int = 20,
    articuloNumero: Optional[int] = None,
    includeRaw: bool = False,
) -> Any:
    result = ordina_api.jurislex_buscar_articulos(
        categoria=categoria,
        idLegislacion=idLegislacion,
        desc=desc,
        soloArticulo=soloArticulo,
        indice=indice,
        elementos=elementos,
        articuloNumero=articuloNumero,
        includeRaw=includeRaw,
    )
    return _unwrap_fastapi_response(result)


def obtener_detalle_articulo_jurislex(
    categoria: int,
    idLegislacion: int,
    idArticulo: int,
    includeRaw: bool = False,
) -> Any:
    result = ordina_api.jurislex_detalle_articulo(
        categoria=categoria,
        idLegislacion=idLegislacion,
        idArticulo=idArticulo,
        includeRaw=includeRaw,
    )
    return _unwrap_fastapi_response(result)


TOOLS: dict[str, dict[str, Any]] = {
    "buscarLey": {
        "description": "Busca leyes por id, categoria o nombre parcial.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "categoria": {"type": "integer"},
                "nombre": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "handler": buscar_ley,
    },
    "buscarJurisprudencia": {
        "description": "Busca jurisprudencia SJF (recomendado: size=10, page=0).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "page": {"type": "integer", "minimum": 0},
                "size": {"type": "integer", "minimum": 1, "maximum": 50},
                "includeRaw": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "handler": buscar_jurisprudencia,
    },
    "obtenerDetalleJurisprudencia": {
        "description": "Obtiene detalle de jurisprudencia por IUS.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ius": {"type": "integer"},
                "isSemanal": {"type": "boolean"},
                "includeRaw": {"type": "boolean"},
            },
            "required": ["ius"],
            "additionalProperties": False,
        },
        "handler": obtener_detalle_jurisprudencia,
    },
    "buscarArticulosJurislex": {
        "description": "Busca articulos Jurislex (requiere categoria e idLegislacion).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "categoria": {"type": "integer"},
                "idLegislacion": {"type": "integer"},
                "desc": {"type": "string"},
                "soloArticulo": {"type": "boolean"},
                "indice": {"type": "integer", "minimum": 0},
                "elementos": {"type": "integer", "minimum": 1, "maximum": 50},
                "articuloNumero": {"type": "integer"},
                "includeRaw": {"type": "boolean"},
            },
            "required": ["categoria", "idLegislacion"],
            "additionalProperties": False,
        },
        "handler": buscar_articulos_jurislex,
    },
    "obtenerDetalleArticuloJurislex": {
        "description": "Obtiene detalle de un articulo Jurislex por idArticulo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "categoria": {"type": "integer"},
                "idLegislacion": {"type": "integer"},
                "idArticulo": {"type": "integer"},
                "includeRaw": {"type": "boolean"},
            },
            "required": ["categoria", "idLegislacion", "idArticulo"],
            "additionalProperties": False,
        },
        "handler": obtener_detalle_articulo_jurislex,
    },
}


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def _read_message() -> Optional[dict[str, Any]]:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("ascii", errors="replace").strip()
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        return None

    body = sys.stdin.buffer.read(content_length)
    if not body:
        return None
    return json.loads(body.decode("utf-8", errors="replace"))


def _ok(id_value: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_value, "result": result}


def _err(id_value: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error_obj = {"code": code, "message": message}
    if data is not None:
        error_obj["data"] = data
    return {"jsonrpc": "2.0", "id": id_value, "error": error_obj}


def _dispatch(method: str, params: dict[str, Any]) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "Ordina-engine", "version": "1.0.0"},
        }

    if method == "tools/list":
        tools = []
        for name, meta in TOOLS.items():
            tools.append(
                {
                    "name": name,
                    "description": meta["description"],
                    "inputSchema": meta["inputSchema"],
                }
            )
        return {"tools": tools}

    if method == "tools/call":
        name = str(params.get("name") or "")
        args_raw = params.get("arguments")
        args = args_raw if isinstance(args_raw, dict) else {}
        meta = TOOLS.get(name)
        if meta is None:
            raise ValueError(f"Tool no encontrada: {name}")

        handler: Callable[..., Any] = meta["handler"]
        result = handler(**args)
        text_result = json.dumps(result, ensure_ascii=False)
        return {
            "content": [{"type": "text", "text": text_result}],
            "structuredContent": result,
            "isError": False,
        }

    if method == "ping":
        return {}

    raise NotImplementedError(f"Metodo no soportado: {method}")


def run_stdio_server() -> None:
    while True:
        message = _read_message()
        if message is None:
            break

        method = message.get("method")
        request_id = message.get("id")

        if not method:
            if request_id is not None:
                _write_message(_err(request_id, -32600, "Invalid Request"))
            continue

        if method == "notifications/initialized":
            continue

        params_raw = message.get("params")
        params: dict[str, Any] = params_raw if isinstance(params_raw, dict) else {}

        if request_id is None:
            try:
                _dispatch(str(method), params)
            except Exception:
                pass
            continue

        try:
            result = _dispatch(str(method), params)
            _write_message(_ok(request_id, result))
        except NotImplementedError as exc:
            _write_message(_err(request_id, -32601, str(exc)))
        except TypeError as exc:
            _write_message(_err(request_id, -32602, "Parametros invalidos", str(exc)))
        except ValueError as exc:
            _write_message(_err(request_id, -32602, str(exc)))
        except Exception as exc:
            _write_message(_err(request_id, -32000, "Error interno", str(exc)))


if __name__ == "__main__":
    run_stdio_server()
