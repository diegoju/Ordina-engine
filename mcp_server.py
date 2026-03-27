from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi.responses import JSONResponse

import api as ordina_api


ROOT = Path(__file__).resolve().parent
VERSION = "1.1.0"
DEFAULT_SJF_HOST = "https://sjf2.scjn.gob.mx"


def _unwrap_fastapi_response(result: Any) -> Any:
    if not isinstance(result, JSONResponse):
        return result

    raw = bytes(result.body).decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"rawText": raw}

    if result.status_code >= 400:
        error_code = "UPSTREAM_REQUEST_FAILED"
        if result.status_code == 404:
            error_code = "NOT_FOUND"
        elif result.status_code == 504:
            error_code = "UPSTREAM_TIMEOUT"
        elif result.status_code == 502:
            error_code = "UPSTREAM_UNAVAILABLE"
        return {
            "ok": False,
            "error": {
                "code": error_code,
                "message": "Upstream request failed",
                "status": result.status_code,
                "upstream": payload,
            },
        }
    return payload


def _result_is_error(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("ok") is False and isinstance(payload.get("error"), dict)


def _top_items(items: Any, limit: int = 3) -> list[Any]:
    if not isinstance(items, list):
        return []
    return items[: max(0, limit)]


def _law_matches(nombre: str) -> list[dict[str, Any]]:
    result = buscar_ley(nombre=nombre)
    if _result_is_error(result):
        raise ValueError(result["error"]["message"])
    if not isinstance(result, list):
        return []
    return result


def _rank_laws(nombre: str, leyes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nombre_norm = (nombre or "").strip().lower()
    if not nombre_norm:
        return leyes

    def score(item: dict[str, Any]) -> tuple[int, int, str]:
        law_name = str(item.get("nombre") or "")
        law_norm = law_name.lower()
        if law_norm == nombre_norm:
            return (0, len(law_name), law_norm)
        if law_norm.startswith(nombre_norm):
            return (1, len(law_name), law_norm)
        if nombre_norm in law_norm:
            return (2, len(law_name), law_norm)
        return (3, len(law_name), law_norm)

    return sorted(leyes, key=score)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_article_number(text: str) -> Optional[int]:
    if not text:
        return None
    match = re.search(r"\bart(?:iculo|\.)?\s+(\d+)\b", text, flags=re.IGNORECASE)
    if match:
        return _safe_int(match.group(1), 0) or None
    match = re.search(r"\b(\d+)\b", text)
    if match:
        return _safe_int(match.group(1), 0) or None
    return None


def _extract_law_hint(text: str) -> Optional[str]:
    if not text:
        return None
    patterns = [
        r"de la ([^.,;]+)",
        r"del ([^.,;]+)",
        r"en la ([^.,;]+)",
        r"en el ([^.,;]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                return candidate
    return None


def _build_consulta_summary(strategy_used: str, result: Any) -> str:
    if _result_is_error(result):
        error = result["error"]
        return f'Fallo la estrategia {strategy_used}: {error.get("code")} (status={error.get("status")})'

    if not isinstance(result, dict):
        return f"Consulta resuelta con estrategia {strategy_used}"

    nested_raw = result.get("result") if isinstance(result.get("result"), dict) else result
    nested = nested_raw if isinstance(nested_raw, dict) else {}

    if strategy_used == "articulo":
        selected_law = nested.get("selectedLaw") or {}
        detail = nested.get("detail") or {}
        if detail:
            return (
                f'Articulo localizado en {selected_law.get("nombre", "ley desconocida")}: '
                f'{detail.get("titulo") or detail.get("idArticulo") or "detalle disponible"}'
            )
        return f'Busqueda de articulo en {selected_law.get("nombre", "ley desconocida")} sin detalle'

    if strategy_used == "jurisprudencia":
        selected = nested.get("selectedItem") or {}
        return f'Jurisprudencia localizada con IUS {selected.get("ius", "?")}'

    if strategy_used == "precedente":
        return f'Precedentes localizados: {nested.get("count", 0)} coincidencias'

    selected_law = nested.get("selectedLaw") or {}
    if selected_law:
        return f'Ley localizada: {selected_law.get("nombre", "sin nombre")}'
    return f'Consulta resuelta con estrategia {strategy_used}'


def _infer_consulta_strategy(
    consulta: str,
    estrategia: str,
    nombre_ley: Optional[str],
    numero_articulo: Optional[int],
) -> str:
    estrategia_norm = (estrategia or "auto").strip().lower()
    if estrategia_norm in {"ley", "articulo", "jurisprudencia", "precedente"}:
        return estrategia_norm

    consulta_norm = (consulta or "").strip().lower()
    if numero_articulo is not None or nombre_ley:
        return "articulo"
    if any(token in consulta_norm for token in ["articulo ", "art. ", "art "]):
        return "articulo"
    if any(token in consulta_norm for token in ["jurisprudencia", "ius", "tesis", "sjf"]):
        return "jurisprudencia"
    if any(token in consulta_norm for token in ["precedente", "precedentes", "ejecutoria", "ejecutorias"]):
        return "precedente"
    return "ley"


def _infer_consulta_metadata(
    consulta: str,
    estrategia: str,
    nombre_ley: Optional[str],
    numero_articulo: Optional[int],
) -> dict[str, Any]:
    consulta_norm = (consulta or "").strip().lower()
    inferred_numero = numero_articulo if numero_articulo is not None else _extract_article_number(consulta)
    inferred_law = nombre_ley or _extract_law_hint(consulta)
    strategy = _infer_consulta_strategy(consulta, estrategia, inferred_law, inferred_numero)

    reasons = []
    confidence = "media"
    if strategy == "articulo":
        if inferred_numero is not None:
            reasons.append("Se detecto numero de articulo en la consulta")
        if inferred_law:
            reasons.append("Se detecto una pista de ley en la consulta")
        confidence = "alta" if inferred_numero is not None else "media"
    elif strategy == "jurisprudencia":
        reasons.append("La consulta menciona terminos de jurisprudencia o tesis")
        confidence = "alta"
    elif strategy == "precedente":
        reasons.append("La consulta menciona precedentes o ejecutorias")
        confidence = "alta"
    else:
        reasons.append("Se usara resolucion de ley por nombre como estrategia base")

    return {
        "strategy": strategy,
        "confidence": confidence,
        "reasons": reasons,
        "nombreLey": inferred_law,
        "numeroArticulo": inferred_numero,
    }


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _resource_catalog_preview(limit: int = 25) -> str:
    leyes = getattr(ordina_api, "leyes", [])
    if not isinstance(leyes, list):
        return "Catalogo no disponible"
    rows = []
    for item in leyes[: max(0, limit)]:
        rows.append(
            f'- id={item.get("id")} categoria={item.get("categoria")} nombre={item.get("nombre")}'
        )
    return "\n".join(rows)


def _summary_text(result: Any) -> str:
    if _result_is_error(result):
        error = result["error"]
        return f'Error {error.get("code")}: {error.get("message")} (status={error.get("status")})'

    if isinstance(result, list):
        return f"{len(result)} resultados"

    if not isinstance(result, dict):
        return str(result)

    if "status" in result and "service" in result and "checks" not in result:
        return f'Servicio {result.get("service")}: {result.get("status")}'

    if "status" in result and "checks" in result:
        return f'Health profundo {result.get("status")} con {len(result.get("checks") or [])} checks'

    if "selectedLaw" in result:
        selected = result.get("selectedLaw") or {}
        count = result.get("count", 0)
        return f'Ley seleccionada: {selected.get("nombre", "sin ley")} ({count} articulos)' 

    if "selectedItem" in result:
        item = result.get("selectedItem") or {}
        return f'Jurisprudencia seleccionada IUS {item.get("ius", "?")}'

    if "strategyUsed" in result and "query" in result:
        return result.get("summary") or f'Consulta resuelta con estrategia {result.get("strategyUsed")}'

    if "items" in result and "count" in result:
        extra = []
        if result.get("query"):
            extra.append(f'query="{result.get("query")}"')
        if result.get("count") is not None:
            extra.append(f'count={result.get("count")}')
        if result.get("total") is not None:
            extra.append(f'total={result.get("total")}')
        return ", ".join(extra) if extra else "Operacion completada"

    if "titulo" in result and "textoPlano" in result:
        return f'Detalle obtenido: {result.get("titulo") or result.get("rubro") or "sin titulo"}'

    return json.dumps(result, ensure_ascii=False)


def _tool_success(result: Any) -> dict[str, Any]:
    text_result = _summary_text(result)
    return {
        "content": [{"type": "text", "text": text_result}],
        "structuredContent": result,
        "isError": _result_is_error(result),
    }


def health() -> Any:
    return ordina_api.health_check()


def health_profundo() -> Any:
    return _unwrap_fastapi_response(ordina_api.deep_health_check())


def buscar_ley(id: Optional[int] = None, categoria: Optional[int] = None, nombre: Optional[str] = None) -> Any:
    result = ordina_api.buscar_ley(id=id, categoria=categoria, nombre=nombre)
    return _unwrap_fastapi_response(result)


def resolver_ley_por_nombre(nombre: str, maxResultados: int = 5) -> Any:
    leyes = _rank_laws(nombre, _law_matches(nombre))
    top = leyes[: max(1, min(maxResultados, 20))]
    return {
        "query": nombre,
        "count": len(top),
        "selectedLaw": top[0] if top else None,
        "items": top,
        "warnings": [] if top else ["No se encontraron leyes coincidentes"],
    }


def buscar_jurisprudencia(q: str = "", page: int = 0, size: int = 10, includeRaw: bool = False) -> Any:
    result = ordina_api.sjf_search(q=q, page=page, size=size, includeRaw=includeRaw)
    return _unwrap_fastapi_response(result)


def buscar_jurisprudencia_avanzada(
    payload: dict[str, Any],
    page: int = 0,
    size: int = 10,
    includeRaw: bool = False,
) -> Any:
    result = ordina_api.sjf_search_advanced(page=page, size=size, includeRaw=includeRaw, payload=payload)
    return _unwrap_fastapi_response(result)


def obtener_detalle_jurisprudencia(
    ius: int,
    isSemanal: Optional[bool] = None,
    includeRaw: bool = False,
    debug: bool = False,
) -> Any:
    result = ordina_api.sjf_detail(
        ius=ius,
        isSemanal=isSemanal,
        hostName=DEFAULT_SJF_HOST,
        includeRaw=includeRaw,
        debug=debug,
    )
    return _unwrap_fastapi_response(result)


def buscar_y_detallar_jurisprudencia(
    q: str,
    page: int = 0,
    size: int = 10,
    matchIndex: int = 0,
    includeRaw: bool = False,
    debug: bool = False,
) -> Any:
    search = buscar_jurisprudencia(q=q, page=page, size=size, includeRaw=False)
    if _result_is_error(search):
        return search

    items = search.get("items") if isinstance(search, dict) else []
    if not items:
        return {
            "query": q,
            "count": 0,
            "items": [],
            "selectedItem": None,
            "detail": None,
            "warnings": ["No se encontraron resultados de jurisprudencia"],
        }

    index = max(0, min(matchIndex, len(items) - 1))
    selected = items[index]
    detail = obtener_detalle_jurisprudencia(
        ius=_safe_int(selected.get("ius"), 0),
        includeRaw=includeRaw,
        debug=debug,
    )
    return {
        "query": q,
        "count": len(items),
        "selectedIndex": index,
        "selectedItem": selected,
        "topMatches": _top_items(items, 3),
        "detail": detail,
    }


def buscar_precedentes(
    q: str = "",
    page: int = 1,
    size: int = 10,
    indice: str = "ejecutorias",
    fuente: str = "SJF",
    extractos: int = 200,
    semantica: int = 0,
    includeRaw: bool = False,
) -> Any:
    result = ordina_api.scjn_precedentes_buscar(
        q=q,
        page=page,
        size=size,
        indice=indice,
        fuente=fuente,
        extractos=extractos,
        semantica=semantica,
        includeRaw=includeRaw,
    )
    return _unwrap_fastapi_response(result)


def buscar_precedentes_avanzado(payload: dict[str, Any], includeRaw: bool = False) -> Any:
    result = ordina_api.scjn_precedentes_buscar_post(includeRaw=includeRaw, payload=payload)
    return _unwrap_fastapi_response(result)


def buscar_decretos_jurislex(idLegislacion: int, idOrdenamiento: Optional[int] = None) -> Any:
    result = ordina_api.jurislex_decretos(idLegislacion=idLegislacion, idOrdenamiento=idOrdenamiento)
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


def buscar_articulos_jurislex_avanzado(payload: dict[str, Any]) -> Any:
    result = ordina_api.jurislex_buscar_articulos_post(payload=payload)
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


def buscar_articulo_por_ley_y_numero(
    nombreLey: str,
    numeroArticulo: int,
    maxLeyes: int = 5,
    includeRaw: bool = False,
) -> Any:
    leyes = _rank_laws(nombreLey, _law_matches(nombreLey))[: max(1, min(maxLeyes, 20))]
    if not leyes:
        return {
            "query": {"nombreLey": nombreLey, "numeroArticulo": numeroArticulo},
            "selectedLaw": None,
            "count": 0,
            "items": [],
            "warnings": ["No se encontro una ley coincidente"],
        }

    for ley in leyes:
        categoria = ley.get("categoria")
        id_legislacion = ley.get("id")
        if categoria is None or id_legislacion is None:
            continue
        search = buscar_articulos_jurislex(
            categoria=int(categoria),
            idLegislacion=int(id_legislacion),
            desc=str(numeroArticulo),
            soloArticulo=True,
            articuloNumero=int(numeroArticulo),
            elementos=10,
            includeRaw=includeRaw,
        )
        if _result_is_error(search):
            return search
        if isinstance(search, dict) and search.get("count", 0) > 0:
            return {
                "query": {"nombreLey": nombreLey, "numeroArticulo": numeroArticulo},
                "selectedLaw": ley,
                "count": search.get("count", 0),
                "items": search.get("items", []),
                "topLawMatches": leyes,
            }

    return {
        "query": {"nombreLey": nombreLey, "numeroArticulo": numeroArticulo},
        "selectedLaw": leyes[0],
        "count": 0,
        "items": [],
        "topLawMatches": leyes,
        "warnings": ["Se encontro la ley, pero no hubo articulos coincidentes con ese numero"],
    }


def obtener_articulo_por_ley_y_numero(
    nombreLey: str,
    numeroArticulo: int,
    maxLeyes: int = 5,
    includeRaw: bool = False,
) -> Any:
    search = buscar_articulo_por_ley_y_numero(
        nombreLey=nombreLey,
        numeroArticulo=numeroArticulo,
        maxLeyes=maxLeyes,
        includeRaw=includeRaw,
    )
    if _result_is_error(search):
        return search

    items = search.get("items") if isinstance(search, dict) else []
    selected_law = search.get("selectedLaw") if isinstance(search, dict) else None
    if not items or not isinstance(selected_law, dict):
        return {
            **(search if isinstance(search, dict) else {}),
            "detail": None,
        }

    top_item = items[0]
    categoria = _safe_int(selected_law.get("categoria"), 0)
    id_legislacion = _safe_int(selected_law.get("id"), 0)
    id_articulo = _safe_int(top_item.get("idArticulo"), 0)
    detail = obtener_detalle_articulo_jurislex(
        categoria=categoria,
        idLegislacion=id_legislacion,
        idArticulo=id_articulo,
        includeRaw=includeRaw,
    )
    return {
        **search,
        "selectedItem": top_item,
        "detail": detail,
    }


def consulta_juridica_completa(
    consulta: str,
    estrategia: str = "auto",
    nombreLey: Optional[str] = None,
    numeroArticulo: Optional[int] = None,
    matchIndex: int = 0,
    includeRaw: bool = False,
) -> Any:
    metadata = _infer_consulta_metadata(consulta, estrategia, nombreLey, numeroArticulo)
    strategy_used = metadata["strategy"]
    resolved_nombre_ley = metadata["nombreLey"]
    resolved_numero_articulo = metadata["numeroArticulo"]

    if strategy_used == "articulo":
        if resolved_numero_articulo is None:
            response = {
                "query": consulta,
                "strategyUsed": strategy_used,
                "confidence": metadata["confidence"],
                "reasons": metadata["reasons"],
                "warnings": ["Para estrategia de articulo necesitas `numeroArticulo` o una consulta mas estructurada"],
                "result": None,
            }
            response["summary"] = _build_consulta_summary(strategy_used, response)
            return response
        result = obtener_articulo_por_ley_y_numero(
            nombreLey=resolved_nombre_ley or consulta,
            numeroArticulo=resolved_numero_articulo,
            includeRaw=includeRaw,
        )
        response = {
            "query": consulta,
            "strategyUsed": strategy_used,
            "confidence": metadata["confidence"],
            "reasons": metadata["reasons"],
            "resolvedNombreLey": resolved_nombre_ley,
            "resolvedNumeroArticulo": resolved_numero_articulo,
            "result": result,
        }
        response["summary"] = _build_consulta_summary(strategy_used, response)
        return response

    if strategy_used == "jurisprudencia":
        result = buscar_y_detallar_jurisprudencia(
            q=consulta,
            matchIndex=matchIndex,
            includeRaw=includeRaw,
        )
        response = {
            "query": consulta,
            "strategyUsed": strategy_used,
            "confidence": metadata["confidence"],
            "reasons": metadata["reasons"],
            "result": result,
        }
        response["summary"] = _build_consulta_summary(strategy_used, response)
        return response

    if strategy_used == "precedente":
        result = buscar_precedentes(q=consulta, includeRaw=includeRaw)
        response = {
            "query": consulta,
            "strategyUsed": strategy_used,
            "confidence": metadata["confidence"],
            "reasons": metadata["reasons"],
            "result": result,
        }
        response["summary"] = _build_consulta_summary(strategy_used, response)
        return response

    law_matches = resolver_ley_por_nombre(nombreLey or consulta)
    response = {
        "query": consulta,
        "strategyUsed": strategy_used,
        "confidence": metadata["confidence"],
        "reasons": metadata["reasons"],
        "result": law_matches,
    }
    response["summary"] = _build_consulta_summary(strategy_used, response)
    return response


TOOLS: dict[str, dict[str, Any]] = {
    "health": {
        "description": "Revisa el estado basico del servicio Ordina-engine.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": health,
    },
    "healthDeep": {
        "description": "Revisa salud profunda de catalogo, SJF y Jurislex.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": health_profundo,
    },
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
    "resolverLeyPorNombre": {
        "description": "Resuelve la ley mas probable por nombre y devuelve mejores coincidencias.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string"},
                "maxResultados": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["nombre"],
            "additionalProperties": False,
        },
        "handler": resolver_ley_por_nombre,
    },
    "buscarJurisprudencia": {
        "description": "Busca jurisprudencia SJF con parametros recomendados para agentes.",
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
    "buscarJurisprudenciaAvanzada": {
        "description": "Ejecuta busqueda avanzada SJF con payload completo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payload": {"type": "object"},
                "page": {"type": "integer", "minimum": 0},
                "size": {"type": "integer", "minimum": 1, "maximum": 50},
                "includeRaw": {"type": "boolean"},
            },
            "required": ["payload"],
            "additionalProperties": False,
        },
        "handler": buscar_jurisprudencia_avanzada,
    },
    "obtenerDetalleJurisprudencia": {
        "description": "Obtiene detalle de jurisprudencia por IUS.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ius": {"type": "integer"},
                "isSemanal": {"type": "boolean"},
                "includeRaw": {"type": "boolean"},
                "debug": {"type": "boolean"},
            },
            "required": ["ius"],
            "additionalProperties": False,
        },
        "handler": obtener_detalle_jurisprudencia,
    },
    "buscarYDetallarJurisprudencia": {
        "description": "Busca jurisprudencia y trae el detalle del mejor match o del indice pedido.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "page": {"type": "integer", "minimum": 0},
                "size": {"type": "integer", "minimum": 1, "maximum": 50},
                "matchIndex": {"type": "integer", "minimum": 0},
                "includeRaw": {"type": "boolean"},
                "debug": {"type": "boolean"},
            },
            "required": ["q"],
            "additionalProperties": False,
        },
        "handler": buscar_y_detallar_jurisprudencia,
    },
    "buscarPrecedentes": {
        "description": "Busca precedentes o ejecutorias SCJN.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "page": {"type": "integer", "minimum": 1},
                "size": {"type": "integer", "minimum": 1, "maximum": 50},
                "indice": {"type": "string"},
                "fuente": {"type": "string"},
                "extractos": {"type": "integer", "minimum": 0, "maximum": 1000},
                "semantica": {"type": "integer", "minimum": 0, "maximum": 1},
                "includeRaw": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "handler": buscar_precedentes,
    },
    "buscarPrecedentesAvanzado": {
        "description": "Ejecuta busqueda avanzada de precedentes SCJN con payload completo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payload": {"type": "object"},
                "includeRaw": {"type": "boolean"},
            },
            "required": ["payload"],
            "additionalProperties": False,
        },
        "handler": buscar_precedentes_avanzado,
    },
    "buscarDecretosJurislex": {
        "description": "Obtiene decretos y anexos Jurislex por legislacion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "idLegislacion": {"type": "integer"},
                "idOrdenamiento": {"type": "integer"},
            },
            "required": ["idLegislacion"],
            "additionalProperties": False,
        },
        "handler": buscar_decretos_jurislex,
    },
    "buscarArticulosJurislex": {
        "description": "Busca articulos Jurislex por categoria e idLegislacion.",
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
    "buscarArticulosJurislexAvanzado": {
        "description": "Ejecuta busqueda avanzada Jurislex con body completo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payload": {"type": "object"},
            },
            "required": ["payload"],
            "additionalProperties": False,
        },
        "handler": buscar_articulos_jurislex_avanzado,
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
    "buscarArticuloPorLeyYNumero": {
        "description": "Resuelve una ley por nombre y busca articulos exactos por numero.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "nombreLey": {"type": "string"},
                "numeroArticulo": {"type": "integer"},
                "maxLeyes": {"type": "integer", "minimum": 1, "maximum": 20},
                "includeRaw": {"type": "boolean"},
            },
            "required": ["nombreLey", "numeroArticulo"],
            "additionalProperties": False,
        },
        "handler": buscar_articulo_por_ley_y_numero,
    },
    "obtenerArticuloPorLeyYNumero": {
        "description": "Resuelve una ley por nombre, busca un articulo exacto y devuelve tambien el detalle completo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "nombreLey": {"type": "string"},
                "numeroArticulo": {"type": "integer"},
                "maxLeyes": {"type": "integer", "minimum": 1, "maximum": 20},
                "includeRaw": {"type": "boolean"}
            },
            "required": ["nombreLey", "numeroArticulo"],
            "additionalProperties": False,
        },
        "handler": obtener_articulo_por_ley_y_numero,
    },
    "consultaJuridicaCompleta": {
        "description": "Decide si conviene consultar leyes, articulos, jurisprudencia o precedentes y ejecuta el flujo base.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "consulta": {"type": "string"},
                "estrategia": {"type": "string", "enum": ["auto", "ley", "articulo", "jurisprudencia", "precedente"]},
                "nombreLey": {"type": "string"},
                "numeroArticulo": {"type": "integer"},
                "matchIndex": {"type": "integer", "minimum": 0},
                "includeRaw": {"type": "boolean"}
            },
            "required": ["consulta"],
            "additionalProperties": False,
        },
        "handler": consulta_juridica_completa,
    },
}


RESOURCES: dict[str, dict[str, Any]] = {
    "ordina://readme": {
        "name": "README Ordina-engine",
        "description": "Documentacion principal del proyecto.",
        "mimeType": "text/markdown",
        "loader": lambda: _read_text_file(ROOT / "README.md"),
    },
    "ordina://instrucciones-minimas": {
        "name": "Instrucciones minimas",
        "description": "Flujos recomendados para consultas juridicas.",
        "mimeType": "text/markdown",
        "loader": lambda: _read_text_file(ROOT / "Ordina-instrucciones-minimas.md"),
    },
    "ordina://openapi/hub": {
        "name": "OpenAPI principal",
        "description": "Contrato OpenAPI principal de Ordina-engine.",
        "mimeType": "text/yaml",
        "loader": lambda: _read_text_file(ROOT / "openapi-ordina-hub.yaml"),
    },
    "ordina://catalogo/preview": {
        "name": "Preview catalogo leyes",
        "description": "Primeras leyes del catalogo local para exploracion rapida.",
        "mimeType": "text/plain",
        "loader": _resource_catalog_preview,
    },
}


PROMPTS: dict[str, dict[str, Any]] = {
    "consulta-juridica-segura": {
        "description": "Guia para consultar Ordina sin saltarse pasos clave.",
        "arguments": [],
        "builder": lambda _args: (
            "Usa Ordina-engine con este flujo: 1) si quieres delegar la decision inicial, usa consultaJuridicaCompleta; "
            "2) si la consulta menciona una ley, primero usa buscarLey "
            "o resolverLeyPorNombre; 2) para articulos usa buscarArticuloPorLeyYNumero o buscarArticulosJurislex; "
            "3) para jurisprudencia usa buscarJurisprudencia y luego obtenerDetalleJurisprudencia; "
            "4) para precedentes usa buscarPrecedentes; 5) si no hay resultados, dilo claramente y no inventes datos."
        ),
    },
    "buscar-articulo": {
        "description": "Prompt guiado para localizar un articulo dentro de una ley.",
        "arguments": [
            {"name": "nombreLey", "required": True},
            {"name": "numeroArticulo", "required": True},
        ],
        "builder": lambda args: (
            f'Busca el articulo {args.get("numeroArticulo")} de la ley "{args.get("nombreLey")}". '
            "Primero resuelve la ley por nombre y luego usa obtenerArticuloPorLeyYNumero para traer el detalle completo."
        ),
    },
    "buscar-jurisprudencia": {
        "description": "Prompt guiado para buscar y resumir jurisprudencia SJF.",
        "arguments": [{"name": "tema", "required": True}],
        "builder": lambda args: (
            f'Busca jurisprudencia SJF sobre "{args.get("tema")}" con size=10 y page=0. '
            "Resume primero los mejores resultados y despues trae detalle del match mas relevante."
        ),
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
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {"name": "Ordina-engine", "version": VERSION},
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
        return _tool_success(result)

    if method == "resources/list":
        resources = []
        for uri, meta in RESOURCES.items():
            resources.append(
                {
                    "uri": uri,
                    "name": meta["name"],
                    "description": meta["description"],
                    "mimeType": meta["mimeType"],
                }
            )
        return {"resources": resources}

    if method == "resources/read":
        uri = str(params.get("uri") or "")
        meta = RESOURCES.get(uri)
        if meta is None:
            raise ValueError(f"Resource no encontrado: {uri}")
        text = meta["loader"]()
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": meta["mimeType"],
                    "text": text,
                }
            ]
        }

    if method == "prompts/list":
        prompts = []
        for name, meta in PROMPTS.items():
            prompts.append(
                {
                    "name": name,
                    "description": meta["description"],
                    "arguments": meta["arguments"],
                }
            )
        return {"prompts": prompts}

    if method == "prompts/get":
        name = str(params.get("name") or "")
        args_raw = params.get("arguments")
        args = args_raw if isinstance(args_raw, dict) else {}
        meta = PROMPTS.get(name)
        if meta is None:
            raise ValueError(f"Prompt no encontrado: {name}")
        text = meta["builder"](args)
        return {
            "description": meta["description"],
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
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
