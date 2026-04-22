from fastapi import Body, FastAPI, Query, Request
import base64
import hashlib
import html
import httpx
import io
import json
import logging
import re
import threading
import unicodedata
import zipfile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import time
from urllib import parse
from typing import Any, Optional
from xml.etree import ElementTree

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ordina")

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_raw_origins = os.getenv("ALLOWED_ORIGINS", "")
_allowed_origins: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = (request.client.host if request.client else None) or "unknown"
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW
    with _rate_lock:
        timestamps = _rate_buckets.setdefault(client_ip, [])
        # Evict expired timestamps
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)
        if len(timestamps) >= _RATE_LIMIT_MAX:
            return JSONResponse(
                status_code=429,
                content={"error": "rate limit exceeded", "retryAfterSeconds": _RATE_LIMIT_WINDOW},
            )
        timestamps.append(now)
    return await call_next(request)

with open(os.path.join(BASE_DIR, "IdLegislaciones.json"), encoding="utf-8") as f:
    leyes = json.load(f)

SJF_BASE = os.getenv("SJF_BASE", "https://sjf2.scjn.gob.mx/services/sjftesismicroservice/api/public")
JURISLEX_BASE = os.getenv("JURISLEX_BASE", "https://jurislex.scjn.gob.mx/Legislaciones.Datos64/Aplicacion/Legislaciones.svc/web")
BJ_SCJN_BASE = os.getenv("BJ_SCJN_BASE", "https://bj.scjn.gob.mx/api/v1/bj")

# Compiled regex patterns — built once at import time
_RE_WHITESPACE = re.compile(r"\s+")
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_EXCESS_NEWLINES = re.compile(r"\n{3,}")
_RE_REGISTRO_DIGITAL = re.compile(r"\bregistro\s+digital\s+(\d{6,8})\b", re.IGNORECASE)
_RE_JURIS_CLAVE = re.compile(r"\b(?:jurisprudencia|tesis(?:\s+aislada)?|criterio\s+aislad[oa])\s+([A-Z0-9][A-Z0-9./\- ]{4,50}\d(?:\s*\([^\)]+\))?)", re.IGNORECASE)
_RE_J_CLAVE_COMPACTA = re.compile(r"\b(?:P\.|[12]a\.)/?J\.\s*\d+/\d{4}(?:\s*\(\d{1,2}a\.\))?(?!\w)", re.IGNORECASE)
_RE_TESIS_AISLADA_CLAVE = re.compile(r"\b(?:criterio\s+aislad[oa]|tesis\s+aislada)\s+((?:P\.|[12]a\.)\s*[A-Z]{1,6}/\d{4}(?:\s*\(\d{1,2}a\.\))?)", re.IGNORECASE)
_RE_ARTICULO_LEY = re.compile(
    r"\b((?:art(?:[íi]culo|\.)|articulos?)\s+[0-9]+[A-Za-z\-]*(?:\s*(?:,|y|e)\s*[0-9]+[A-Za-z\-]*)*(?:\s+bis|\s+ter|\s+qu[áa]ter)?(?:\s*,?\s*fracci[oó]n\s+[IVXLCDM]+)?)\s+(?:de(?:l| la| los| las)?|en)\s+(.+?)(?=(?:,?\s+(?:(?:y|e)\s+)?(?:(?:el|la|los|las)\s+)?(?:art(?:[íi]culo|\.)|articulos?|jurisprudencia|tesis|criterio\s+aislad[oa]|registro\s+digital)\b)|[.;:\n]|$)",
    re.IGNORECASE,
)
_RE_ARTICULO_CONSTITUCION = re.compile(
    r"\b((?:art(?:[íi]culo|\.)|articulos?)\s+[0-9]+[A-Za-z\-]*(?:\s*(?:,|y|e)\s*[0-9]+[A-Za-z\-]*)*(?:\s+bis|\s+ter|\s+qu[áa]ter)?(?:\s*,?\s*fracci[oó]n\s+[IVXLCDM]+)?)\s+(?:constitucional|de la constituci[oó]n(?:\s+pol[ií]tica\s+de\s+los\s+estados\s+unidos\s+mexicanos)?)",
    re.IGNORECASE,
)
_RE_ABREVIATURA_PARENTESIS = re.compile(
    r"([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ0-9 ,.;:/\-]{10,180}?)\s*\(([A-Z][A-Z0-9.]{1,15})\)",
)
_RE_ABREVIATURA_EN_LO_SUCESIVO = re.compile(
    r"([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ0-9 ,.;:/\-]{10,180}?)(?:,\s*)?en\s+lo\s+sucesivo(?:,\s*)?(?:se\s+denominar[aá]|denominad[oa]\s+como|citad[oa]\s+como)?\s*[\"“”']([A-Z][A-Z0-9.]{1,15})[\"“”']",
    re.IGNORECASE,
)
_RE_ABREVIATURA_GLOSARIO = re.compile(
    r"^\s*([A-Z][A-Z0-9.]{1,15})\s*[:=]\s*([^\n]{6,180})$",
    re.MULTILINE,
)

# TTL response cache — only for successful, read-only upstream queries
_CACHE_TTL: int = int(os.getenv("CACHE_TTL", "300"))  # seconds (default 5 min)
_cache: dict[str, tuple[float, int, Any]] = {}        # key → (timestamp, status, data)
_cache_lock = threading.Lock()


def _cache_key(url: str, method: str, body: Optional[Any]) -> str:
    body_str = json.dumps(body, sort_keys=True, ensure_ascii=False) if body is not None else ""
    raw = f"{method}:{url}:{body_str}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _get_cached(key: str) -> Optional[tuple[int, Any]]:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        ts, status, data = entry
        if time.time() - ts > _CACHE_TTL:
            del _cache[key]
            return None
        return status, data


def _set_cached(key: str, status: int, data: Any) -> None:
    if status >= 400:
        return  # never cache errors
    with _cache_lock:
        _cache[key] = (time.time(), status, data)


# Persistent HTTP client — reuses connections across requests
_HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "35"))
_http_client = httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True)

# Rate limiting — sliding window, no external deps
_RATE_LIMIT_WINDOW: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))   # seconds
_RATE_LIMIT_MAX: int = int(os.getenv("RATE_LIMIT_MAX", "120"))         # requests per window per IP
_rate_buckets: dict[str, list[float]] = {}
_rate_lock = threading.Lock()


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).lower() == "true"


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _RE_WHITESPACE.sub(" ", text).strip().lower()
    return text


def _normalize_search_text(value: str) -> str:
    text = _normalize_text(value)
    text = re.sub(r"[^\w]+", " ", text)
    text = _RE_WHITESPACE.sub(" ", text).strip()
    return text


_LEYES_INDEX = [
    {
        "id": ley.get("id"),
        "categoria": ley.get("categoria"),
        "nombre": ley.get("nombre") or "",
        "nombreNormalizado": _normalize_text(ley.get("nombre") or ""),
    }
    for ley in leyes
    if isinstance(ley, dict) and str(ley.get("nombre") or "").strip()
]
_LEYES_INDEX.sort(key=lambda item: len(item["nombreNormalizado"]), reverse=True)


def _default_sjf_payload(q: str) -> dict:
    payload = {
        "classifiers": [
            {
                "name": "idEpoca",
                "value": ["210", "200", "100", "5", "4", "3", "2", "1"],
                "allSelected": False,
                "visible": False,
                "isMatrix": False,
            },
            {
                "name": "numInstancia",
                "value": ["6", "0", "60", "7", "70", "80", "1", "2", "50", "3", "4", "5"],
                "allSelected": False,
                "visible": False,
                "isMatrix": False,
            },
            {
                "name": "tipoDocumento",
                "value": ["1"],
                "allSelected": False,
                "visible": False,
                "isMatrix": False,
            },
        ],
        "searchTerms": [],
        "bFacet": True,
        "ius": [],
        "idApp": "SJFAPP2020",
        "lbSearch": ["Todo"],
        "filterExpression": "",
    }

    term = (q or "").strip()
    if term:
        payload["searchTerms"].append(
            {
                "expression": term,
                "fields": ["localizacionBusqueda", "rubro", "texto", "precedentes"],
                "fieldsUser": "",
                "fieldsText": "",
                "operator": 0,
                "operatorUser": "Y",
                "operatorText": "Y",
                "lsFields": [],
                "esInicial": True,
                "esNRD": False,
            }
        )
    return payload


_COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:148.0) Gecko/20100101 Firefox/148.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _build_headers(
    origin: str,
    referer: str,
    cookie_env: str,
    content_type: bool = False,
    ct_value: str = "application/json",
) -> dict:
    headers = {**_COMMON_HEADERS, "Origin": origin, "Referer": referer}
    if content_type:
        headers["Content-Type"] = ct_value
    cookie = os.getenv(cookie_env)
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _sjf_headers(content_type: bool = False) -> dict:
    return _build_headers(
        "https://sjf2.scjn.gob.mx",
        "https://sjf2.scjn.gob.mx/listado-resultado-tesis",
        "SJF_COOKIE",
        content_type,
    )


def _jurislex_headers(content_type: bool = False) -> dict:
    return _build_headers(
        "https://jurislex.scjn.gob.mx",
        "https://jurislex.scjn.gob.mx/",
        "JURISLEX_COOKIE",
        content_type,
        ct_value="application/json;charset=utf-8",
    )


def _bj_scjn_headers(content_type: bool = False) -> dict:
    return _build_headers(
        "https://bj.scjn.gob.mx",
        "https://bj.scjn.gob.mx/",
        "BJ_SCJN_COOKIE",
        content_type,
    )


def _http_json(
    url: str,
    method: str = "GET",
    body: Optional[Any] = None,
    headers: Optional[dict] = None,
    use_cache: bool = False,
) -> tuple[int, Any]:
    cache_key = _cache_key(url, method, body) if use_cache else None
    if cache_key:
        cached = _get_cached(cache_key)
        if cached is not None:
            return cached

    content = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        resp = _http_client.request(method=method, url=url, content=content, headers=headers or {})
        try:
            parsed: Any = resp.json()
        except Exception:
            parsed = {"rawText": resp.text}
        status = resp.status_code
        if status < 400 and cache_key:
            _set_cached(cache_key, status, parsed)
        elif status >= 400:
            logger.warning("upstream HTTP error %s for %s %s", status, method, url)
        return status, parsed
    except httpx.TimeoutException as exc:
        logger.error("upstream timeout for %s %s", method, url)
        return 504, {"error": "upstream request timed out", "errorType": type(exc).__name__, "detail": str(exc)}
    except httpx.RequestError as exc:
        logger.error("upstream request error for %s %s: %s", method, url, exc)
        return 502, {"error": "upstream request failed", "errorType": type(exc).__name__, "detail": str(exc)}
    except Exception as exc:
        logger.error("unexpected error for %s %s: %s", method, url, exc)
        return 502, {"error": "upstream request failed", "errorType": type(exc).__name__, "detail": str(exc)}


def _redact_headers(headers: Optional[dict]) -> dict:
    safe_headers = {}
    for key, value in (headers or {}).items():
        if str(key).lower() == "cookie":
            safe_headers[key] = "<redacted>"
        else:
            safe_headers[key] = value
    return safe_headers


def _sjf_detail_attempt(ius: int, host_name: str, is_semanal, include_host_name: bool):
    params = {}
    if include_host_name:
        params["hostName"] = host_name
    if is_semanal is True:
        params["isSemanal"] = "true"
    elif is_semanal is False:
        params["isSemanal"] = "false"

    query = parse.urlencode(params)
    url = f"{SJF_BASE}/tesis/{ius}"
    if query:
        url = f"{url}?{query}"

    headers = _sjf_headers(content_type=False)
    started_at = time.time()
    status, data = _http_json(url, method="GET", headers=headers)
    elapsed_ms = int((time.time() - started_at) * 1000)

    return {
        "status": status,
        "data": data,
        "url": url,
        "isSemanal": is_semanal,
        "hostNameIncluded": include_host_name,
        "durationMs": elapsed_ms,
        "requestHeaders": _redact_headers(headers),
    }


def _sjf_detail_attempts(ius: int, host_name: str, is_semanal: Optional[bool]):
    if is_semanal is None:
        plans = [(True, True), (False, True), (True, False), (False, False)]
    else:
        plans = [(bool(is_semanal), True), (bool(is_semanal), False)]

    attempts = []
    for sem_value, include_host_name in plans:
        attempt = _sjf_detail_attempt(ius, host_name, sem_value, include_host_name)
        attempts.append(attempt)
        if attempt["status"] < 400:
            return attempt, attempts

    return attempts[-1], attempts


def _extract_results(payload: Any, *keys: str) -> list:
    """Return the first list found in payload (or payload["data"]) under any of the given keys."""
    if not isinstance(payload, dict):
        return []
    for key in keys:
        val = payload.get(key)
        if isinstance(val, list):
            return val
    data = payload.get("data")
    if isinstance(data, dict):
        for key in keys:
            val = data.get(key)
            if isinstance(val, list):
                return val
    return []


def _normalize_doc(doc: dict, include_raw: bool = False) -> dict:
    ius = doc.get("ius") or doc.get("registroDigital") or doc.get("id")
    semanal_raw = doc.get("semanal")
    if semanal_raw is None:
        semanal_raw = doc.get("isSemanal")

    if semanal_raw is None:
        is_semanal = None
    else:
        is_semanal = semanal_raw is True or semanal_raw == 1 or str(semanal_raw) == "1"

    rubro = _strip_html(doc.get("rubro") or doc.get("rubroTexto") or "").upper()
    texto = str(doc.get("textoPublicacion") or doc.get("texto") or "")
    texto_snippet = _strip_html(texto)[:500]

    item = {
        "ius": ius,
        "isSemanal": is_semanal,
        "rubro": rubro,
        "fechaPublicacion": doc.get("fechaPublicacion") or doc.get("fecha") or "",
        "instancia": doc.get("instancia") or "",
        "epoca": doc.get("epoca") or "",
        "tipoDocumento": doc.get("tipoDocumento") or "",
        "textoSnippet": texto_snippet,
    }
    if include_raw:
        item["raw"] = doc
    return item


def _to_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def _to_bool(value: Any, fallback: bool = False) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes", "si")


def _strip_html(value: Any) -> str:
    text = str(value or "")
    text = html.unescape(text)
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = text.replace("</p>", "\n\n").replace("<p>", "")
    text = _RE_HTML_TAG.sub("", text)
    text = _RE_EXCESS_NEWLINES.sub("\n\n", text)
    return text.strip()


def _extract_docx_text_from_xml(raw_xml: bytes) -> str:
    try:
        root = ElementTree.fromstring(raw_xml)
    except Exception:
        return ""

    paragraphs: list[str] = []

    for paragraph_node in root.iter():
        if paragraph_node.tag.rsplit("}", 1)[-1] != "p":
            continue
        current_parts: list[str] = []
        for node in paragraph_node.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "t":
                current_parts.append(node.text or "")
            elif tag == "tab":
                current_parts.append("\t")
            elif tag in {"br", "cr"}:
                current_parts.append("\n")
        paragraph = "".join(current_parts).strip()
        if paragraph:
            paragraphs.append(paragraph)

    return "\n\n".join(paragraphs)


def _extract_docx_text(content: bytes) -> str:
    targets = [
        "word/document.xml",
        "word/footnotes.xml",
        "word/endnotes.xml",
        "word/header1.xml",
        "word/header2.xml",
        "word/header3.xml",
        "word/footer1.xml",
        "word/footer2.xml",
        "word/footer3.xml",
    ]
    parts: list[str] = []

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            for target in targets:
                if target not in names:
                    continue
                text = _extract_docx_text_from_xml(archive.read(target))
                if text:
                    parts.append(text)
    except zipfile.BadZipFile:
        return ""
    except Exception:
        return ""

    merged = "\n\n".join(part for part in parts if part.strip())
    return _RE_EXCESS_NEWLINES.sub("\n\n", merged).strip()


def _jurislex_filter_raw(id_legislacion: int, articulo_numero: Optional[int] = None) -> str:
    if articulo_numero is None:
        data = {
            "bool": {
                "should": [
                    {"terms": {"ordenamiento.idOrdenamiento": [int(id_legislacion)]}}
                ]
            }
        }
    else:
        data = {
            "bool": {
                "should": [
                    {
                        "bool": {
                            "must": [
                                {"term": {"ordenamiento.idOrdenamiento": int(id_legislacion)}},
                                {"terms": {"articulos": [int(articulo_numero)]}},
                            ]
                        }
                    }
                ]
            }
        }
    return json.dumps(data, ensure_ascii=False)


def _jurislex_desc_value(desc: str, articulo_numero: Optional[int]) -> str:
    desc_text = str(desc or "").strip()
    if desc_text:
        return desc_text
    if articulo_numero is not None:
        return str(int(articulo_numero))
    return ""


def _jurislex_search_filter_raw(id_legislacion: int, articulo_numero: Optional[int], raw_desc: str) -> str:
    # Jurislex encuentra mejor artículos concretos usando Desc="<numero>" con filtro amplio por ordenamiento.
    if articulo_numero is not None and not str(raw_desc or "").strip():
        return _jurislex_filter_raw(id_legislacion, None)
    return _jurislex_filter_raw(id_legislacion, articulo_numero)


def _normalize_jurislex_result(item: dict, include_raw: bool = False) -> dict:
    normalized = {
        "idArticulo": item.get("iId"),
        "idLegislacion": item.get("iIdLey"),
        "numeroArticulo": item.get("iNumArt"),
        "tipo": item.get("iTipo"),
        "ley": item.get("sDescLey") or "",
        "texto": item.get("sDesc") or "",
        "textoPlano": _strip_html(item.get("sDesc") or ""),
    }
    if include_raw:
        normalized["raw"] = item
    return normalized


# Convenience wrappers kept for clarity at call sites
def _extract_docs(payload: Any) -> list:
    return _extract_results(payload, "documents", "content", "results")


def _extract_bj_items(data: Any) -> list:
    return _extract_results(data, "resultados")


def _extracto_texto(extractos):
    if isinstance(extractos, dict):
        texto = extractos.get("Texto")
        if isinstance(texto, list):
            chunks = [_strip_html(x) for x in texto if str(x or "").strip()]
            return " ... ".join(chunks[:3]).strip()
        if isinstance(texto, str):
            return _strip_html(texto)
    if isinstance(extractos, list):
        chunks = [_strip_html(x) for x in extractos if str(x or "").strip()]
        return " ... ".join(chunks[:3]).strip()
    if isinstance(extractos, str):
        return _strip_html(extractos)
    return ""


def _extract_bj_extractos(extractos: Any, limit: int = 5) -> list[dict]:
    if not isinstance(extractos, dict):
        return []

    items = []
    for tipo, value in extractos.items():
        values = value if isinstance(value, list) else [value]
        for snippet in values:
            texto = _strip_html(snippet)
            if not texto:
                continue
            items.append({"tipo": str(tipo or ""), "texto": texto[:700]})
            if len(items) >= limit:
                return items
    return items


def _normalize_bj_item(item: dict, include_raw: bool = False) -> dict:
    epoca = item.get("epoca") if isinstance(item.get("epoca"), dict) else {}
    localizacion = item.get("localizacion") if isinstance(item.get("localizacion"), dict) else {}
    texto = str(item.get("texto") or "")
    extracto = _extracto_texto(item.get("extractos"))

    normalized = {
        "registroDigital": item.get("registroDigital"),
        "rubro": _strip_html(item.get("rubro") or ""),
        "tipoEjecutoria": item.get("tipoEjecutoria") or "",
        "tipoAsunto": item.get("tipoAsunto") or "",
        "asunto": item.get("asunto") or "",
        "organoJurisdiccional": item.get("organoJurisdiccional") or "",
        "instancia": item.get("instancia") or "",
        "epoca": {
            "numero": epoca.get("numero") or "",
            "nombre": epoca.get("nombre") or "",
        },
        "tesis": item.get("tesis") or "",
        "numeroExpediente": item.get("numeroExpediente") or "",
        "promovente": item.get("promovente") or "",
        "fuente": item.get("fuente") or "",
        "volumen": item.get("volumen") or "",
        "localizacion": {
            "libro": localizacion.get("libro") or "",
            "tomo": localizacion.get("tomo") or "",
            "mes": localizacion.get("mes") or "",
            "anio": localizacion.get("anio") or "",
            "pagina": localizacion.get("pagina") or "",
        },
        "textoSnippet": _strip_html(texto)[:700],
        "extractoSnippet": extracto[:700],
    }
    if include_raw:
        normalized["raw"] = item
    return normalized


def _normalize_bj_legislacion_item(item: dict, include_raw: bool = False) -> dict:
    materias = item.get("materia") if isinstance(item.get("materia"), list) else []
    extractos = _extract_bj_extractos(item.get("extractos"))

    normalized = {
        "id": item.get("id"),
        "ordenamiento": item.get("ordenamiento") or "",
        "categoriaOrdenamiento": item.get("categoriaOrdenamiento") or "",
        "ambito": item.get("ambito") or "",
        "estado": item.get("estado") or "",
        "pais": item.get("pais") or "",
        "vigencia": item.get("vigencia") or "",
        "fechaPublicacion": item.get("fechaPublicado") or "",
        "materias": [str(materia) for materia in materias if str(materia or "").strip()],
        "resumen": _strip_html(item.get("resumen") or ""),
        "extractos": extractos,
        "textoSnippet": (extractos[0]["texto"] if extractos else ""),
    }
    if include_raw:
        normalized["raw"] = item
    return normalized


def _normalize_bj_legislacion_bloque(item: dict, include_raw: bool = False) -> dict:
    normalized = {
        "id": item.get("id"),
        "orden": item.get("orden"),
        "referencia": item.get("referencia") or "",
        "numero": item.get("numero"),
        "vigencia": item.get("vigencia") or "",
        "fechaActualizacion": item.get("fechaActualizacion") or "",
        "articuloVersion": item.get("articuloVersion"),
        "contenido": item.get("contenido") or "",
        "contenidoPlano": _strip_html(item.get("contenido") or ""),
    }
    if include_raw:
        normalized["raw"] = item
    return normalized


def _normalize_bj_legislacion_detail(data: dict, documento_id: int, include_raw: bool = False) -> dict:
    articulos = data.get("articulos") if isinstance(data.get("articulos"), list) else []
    bloques = [_normalize_bj_legislacion_bloque(item, include_raw=include_raw) for item in articulos if isinstance(item, dict)]
    encabezado = next((bloque for bloque in bloques if bloque.get("referencia") == "ENCABEZADO"), None)

    response = {
        "id": data.get("id") or documento_id,
        "ordenamiento": data.get("ordenamiento") or (encabezado or {}).get("contenidoPlano", "").split("\n\n", 1)[0],
        "categoriaOrdenamiento": data.get("categoriaOrdenamiento") or "",
        "ambito": data.get("ambito") or "",
        "estado": data.get("estado") or "",
        "pais": data.get("pais") or "",
        "vigencia": data.get("vigencia") or "",
        "fechaPublicacion": data.get("fechaPublicado") or "",
        "fechaActualizacion": data.get("fechaActualizacion") or "",
        "materias": [str(materia) for materia in (data.get("materia") or []) if str(materia or "").strip()] if isinstance(data.get("materia"), list) else [],
        "resumen": _strip_html(data.get("resumen") or ""),
        "totalBloques": len(bloques),
        "bloques": bloques,
    }
    if include_raw:
        response["raw"] = data
    return response


def _extract_article_numbers(fragment: str) -> list[str]:
    return re.findall(r"\d+[A-Za-z\-]*", str(fragment or ""))


def _resolve_ley_reference(raw_ley: str) -> Optional[dict]:
    ley_norm = _normalize_text(raw_ley)
    if not ley_norm:
        return None

    exact_matches = [candidate for candidate in _LEYES_INDEX if candidate["nombreNormalizado"] == ley_norm]
    if exact_matches:
        return min(exact_matches, key=lambda item: len(item["nombreNormalizado"]))

    contains_matches = [candidate for candidate in _LEYES_INDEX if ley_norm and ley_norm in candidate["nombreNormalizado"]]
    if contains_matches:
        return min(contains_matches, key=lambda item: len(item["nombreNormalizado"]))

    for candidate in _LEYES_INDEX:
        candidate_norm = candidate["nombreNormalizado"]
        if candidate_norm in ley_norm:
            return candidate

    tokens = [token for token in ley_norm.split(" ") if len(token) > 2]
    if not tokens:
        return None

    best_match = None
    best_score = 0
    for candidate in _LEYES_INDEX:
        score = sum(1 for token in tokens if token in candidate["nombreNormalizado"])
        if score > best_score and score >= min(3, len(tokens)):
            best_match = candidate
            best_score = score
    return best_match


def _resolve_constitucion_reference() -> Optional[dict]:
    for candidate in _LEYES_INDEX:
        if candidate["nombreNormalizado"] == "constitucion politica de los estados unidos mexicanos":
            return candidate
    return None


def _resolve_document_law_reference(raw_ley: str) -> Optional[dict]:
    raw = str(raw_ley or "").strip()
    if not raw:
        return None
    raw_norm = _normalize_text(raw)
    if raw_norm in {
        "constitucion politica de los estados unidos mexicanos",
        "constitucion federal",
        "cpeum",
    }:
        return _resolve_constitucion_reference()
    return _resolve_ley_reference(raw)


def _clean_abbreviation(value: str) -> str:
    cleaned = str(value or "").strip().strip("()[]{}.,;: ")
    if not cleaned:
        return ""
    if not re.fullmatch(r"[A-Z][A-Z0-9.]{1,15}", cleaned):
        return ""
    return cleaned


def _register_abbreviation(
    results: list[dict],
    seen: set[tuple[str, str]],
    abbreviation: str,
    candidate_name: str,
    start: int,
    end: int,
    source: str,
) -> None:
    abbr = _clean_abbreviation(abbreviation)
    if not abbr:
        return

    resolved = _resolve_document_law_reference(candidate_name)
    if resolved is None:
        return

    resolved_name = str(resolved.get("nombre") or "").strip()
    detected_name = str(candidate_name or "").strip()
    if resolved_name and _normalize_text(resolved_name) in _normalize_text(detected_name):
        detected_name = resolved_name

    key = (abbr, resolved_name)
    if key in seen:
        return
    seen.add(key)
    results.append(
        {
            "abreviatura": abbr,
            "nombreDetectado": detected_name,
            "nombreResuelto": resolved_name or detected_name,
            "idLegislacion": resolved.get("id"),
            "categoria": resolved.get("categoria"),
            "inicio": start,
            "fin": end,
            "confianza": "alta",
            "fuente": source,
        }
    )


def _extract_document_abbreviations(texto: str) -> list[dict]:
    abbreviations: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for match in _RE_ABREVIATURA_PARENTESIS.finditer(texto):
        _register_abbreviation(
            abbreviations,
            seen,
            match.group(2),
            match.group(1),
            match.start(),
            match.end(),
            "parentesis",
        )

    for match in _RE_ABREVIATURA_EN_LO_SUCESIVO.finditer(texto):
        _register_abbreviation(
            abbreviations,
            seen,
            match.group(2),
            match.group(1),
            match.start(),
            match.end(),
            "enLoSucesivo",
        )

    for match in _RE_ABREVIATURA_GLOSARIO.finditer(texto):
        _register_abbreviation(
            abbreviations,
            seen,
            match.group(1),
            match.group(2),
            match.start(),
            match.end(),
            "glosario",
        )

    abbreviations.sort(key=lambda item: (item.get("inicio", 0), item.get("abreviatura", "")))
    return abbreviations


def _abbreviation_map(abbreviations: list[dict]) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for item in abbreviations:
        abbr = _clean_abbreviation(item.get("abreviatura") or "")
        if abbr and abbr not in mapping:
            mapping[abbr] = item
    return mapping


def _append_cita(citas: list[dict], seen: set[tuple], item: dict) -> None:
    key = (item.get("tipo"), item.get("inicio"), item.get("fin"), item.get("textoOriginal"))
    if key in seen:
        return
    seen.add(key)
    citas.append(item)


def _append_cita_if_not_contained(citas: list[dict], seen: set[tuple], item: dict) -> None:
    clave = str(item.get("clave") or "").strip().lower()
    inicio = item.get("inicio")
    fin = item.get("fin")
    if clave and inicio is not None and fin is not None:
        for existing in citas:
            existing_clave = str(existing.get("clave") or "").strip().lower()
            if existing_clave == clave and existing.get("inicio", -1) <= inicio and existing.get("fin", -1) >= fin:
                return
    _append_cita(citas, seen, item)


def _sjf_exact_match_for_clave(clave: str) -> Optional[dict]:
    clave_norm = _normalize_search_text(clave)
    if not clave_norm:
        return None

    status, data = _http_json(
        f"{SJF_BASE}/tesis?page=0&size=5",
        method="POST",
        body=_default_sjf_payload(clave),
        headers=_sjf_headers(content_type=True),
        use_cache=True,
    )
    if status >= 400:
        return None

    docs = _extract_docs(data)
    for doc in docs:
        candidate = str(doc.get("claveTesis") or doc.get("tesis") or "")
        if _normalize_search_text(candidate) == clave_norm:
            return {
                "ius": doc.get("ius") or doc.get("registroDigital") or doc.get("id"),
                "claveCanonical": candidate,
                "rubro": _strip_html(doc.get("rubro") or ""),
                "fuente": doc.get("fuente") or "SJF",
                "sala": doc.get("sala") or "",
                "tipoTesis": doc.get("tipoTesis") or "",
                "localizacion": doc.get("localizacion") or "",
            }
    return None


def _enrich_jurisprudencial_cita(item: dict) -> dict:
    clave = str(item.get("clave") or "").strip()
    if not clave:
        item["confianza"] = "media"
        item["requiereConfirmacion"] = True
        item["motivoConfirmacion"] = "cita jurisprudencial sin clave verificable"
        item["resuelta"] = False
        return item

    match = _sjf_exact_match_for_clave(clave)
    if match is not None:
        item["resuelta"] = True
        item["confianza"] = "alta"
        item["requiereConfirmacion"] = False
        item["ius"] = match.get("ius")
        item["claveCanonical"] = match.get("claveCanonical")
        item["rubro"] = match.get("rubro")
        item["fuenteProbable"] = match.get("fuente")
        item["localizacion"] = match.get("localizacion")
        return item

    item["resuelta"] = False
    item["confianza"] = "media"
    item["requiereConfirmacion"] = True
    item["motivoConfirmacion"] = "clave detectada sin coincidencia exacta en SJF"
    item["fuenteProbable"] = "SJF"
    item["consultaSugerida"] = clave
    return item


def _resolve_cita_articulo(cita: dict) -> Optional[dict]:
    articulos = cita.get("articulos") or []
    if not articulos:
        return None
    articulo = str(articulos[0])
    nombre = str(cita.get("ley") or cita.get("leyMencionada") or "")
    if not nombre:
        return None

    detail = _normas_articulos_detalle_core(
        nombre=nombre,
        articulo=articulo,
        q=None,
        page=1,
        size=5,
        include_raw=False,
    )
    if isinstance(detail, JSONResponse):
        return None

    articulo_detail = detail.get("articulo") or {}
    return {
        "tipo": "articulo",
        "textoOriginal": cita.get("textoOriginal"),
        "fuenteUsada": detail.get("fuenteUsada") or "",
        "ley": articulo_detail.get("ley") or nombre,
        "numero": articulo_detail.get("numero"),
        "referencia": articulo_detail.get("referencia") or "",
        "libro": articulo_detail.get("libro") or "",
        "titulo": articulo_detail.get("titulo") or "",
        "capitulo": articulo_detail.get("capitulo") or "",
        "texto": articulo_detail.get("textoPlano") or articulo_detail.get("texto") or "",
        "meta": articulo_detail.get("meta") or {},
    }


def _resolve_cita_jurisprudencial(cita: dict) -> Optional[dict]:
    ius = cita.get("ius") or cita.get("registroDigital")
    if ius is None:
        return None
    try:
        ius_value = int(ius)
    except Exception:
        return None

    detail = sjf_detail(ius=ius_value, isSemanal=None, hostName="https://sjf2.scjn.gob.mx", includeRaw=False, debug=False)
    if isinstance(detail, JSONResponse):
        return None

    return {
        "tipo": cita.get("tipo") or "jurisprudencia",
        "textoOriginal": cita.get("textoOriginal"),
        "ius": detail.get("ius") or ius_value,
        "clave": cita.get("claveCanonical") or cita.get("clave") or "",
        "rubro": detail.get("rubro") or cita.get("rubro") or "",
        "fechaPublicacion": detail.get("fechaPublicacion") or "",
        "texto": detail.get("textoPlano") or detail.get("texto") or "",
        "fuenteUsada": "SJF",
    }


def _resolve_cita_detalle(cita: dict) -> Optional[dict]:
    if cita.get("tipo") == "articulo":
        return _resolve_cita_articulo(cita)
    if cita.get("tipo") in {"jurisprudencia", "tesis"}:
        return _resolve_cita_jurisprudencial(cita)
    return None


def _merge_cita_with_detalle(cita: dict, detalle: Optional[dict]) -> dict:
    if detalle is None:
        return cita

    enriched = dict(cita)
    texto_cita = detalle.get("texto") or ""
    if texto_cita:
        enriched["textoCita"] = texto_cita
    if detalle.get("fuenteUsada"):
        enriched["fuenteUsada"] = detalle.get("fuenteUsada")
    if cita.get("tipo") == "articulo":
        if detalle.get("referencia"):
            enriched["referencia"] = detalle.get("referencia")
    elif cita.get("tipo") in {"jurisprudencia", "tesis"}:
        if detalle.get("fechaPublicacion"):
            enriched["fechaPublicacion"] = detalle.get("fechaPublicacion")
        if detalle.get("rubro") and not enriched.get("rubro"):
            enriched["rubro"] = detalle.get("rubro")
        if detalle.get("ius") and not enriched.get("ius"):
            enriched["ius"] = detalle.get("ius")
    return enriched


def _build_citas_report(citas: list[dict]) -> dict:
    articulos_citados = []
    criterios_citados = []
    pendientes = []

    for cita in citas:
        if cita.get("tipo") == "articulo":
            resolved = _resolve_cita_detalle(cita)
            if resolved is not None:
                articulos_citados.append(resolved)
                continue
        elif cita.get("tipo") in {"jurisprudencia", "tesis"}:
            resolved = _resolve_cita_detalle(cita)
            if resolved is not None:
                criterios_citados.append(resolved)
                continue

        if cita.get("requiereConfirmacion") or not cita.get("resuelta"):
            pendientes.append(
                {
                    "tipo": cita.get("tipo") or "",
                    "textoOriginal": cita.get("textoOriginal") or "",
                    "clave": cita.get("clave") or "",
                    "registroDigital": cita.get("registroDigital") or "",
                    "motivo": cita.get("motivoConfirmacion") or "no se pudo resolver automaticamente",
                    "consultaSugerida": cita.get("consultaSugerida") or "",
                }
            )

    return {
        "articulosCitados": articulos_citados,
        "criteriosCitados": criterios_citados,
        "pendientesConfirmacion": pendientes,
    }


def _extract_citas(texto: str, abbreviations: Optional[list[dict]] = None) -> list[dict]:
    citas: list[dict] = []
    seen: set[tuple] = set()
    constitucion = _resolve_constitucion_reference()
    abbreviation_lookup = _abbreviation_map(abbreviations or [])

    for match in _RE_ARTICULO_CONSTITUCION.finditer(texto):
        texto_original = match.group(0)
        articulo_fragmento = match.group(1)
        _append_cita(
            citas,
            seen,
            {
                "tipo": "articulo",
                "subtipo": "constitucion",
                "textoOriginal": texto_original,
                "inicio": match.start(),
                "fin": match.end(),
                "articulos": _extract_article_numbers(articulo_fragmento),
                "ley": (constitucion or {}).get("nombre") or "Constitución Política de los Estados Unidos Mexicanos",
                "idLegislacion": (constitucion or {}).get("id"),
                "categoria": (constitucion or {}).get("categoria"),
                "resuelta": constitucion is not None,
            },
        )

    for match in _RE_ARTICULO_LEY.finditer(texto):
        texto_original = match.group(0)
        articulo_fragmento = match.group(1)
        ley_fragmento = match.group(2).strip(" ,)")
        ley_resuelta = _resolve_ley_reference(ley_fragmento)
        abbreviation = abbreviation_lookup.get(_clean_abbreviation(ley_fragmento))
        if ley_resuelta is None and abbreviation is not None:
            ley_resuelta = {
                "id": abbreviation.get("idLegislacion"),
                "categoria": abbreviation.get("categoria"),
                "nombre": abbreviation.get("nombreResuelto") or abbreviation.get("nombreDetectado") or ley_fragmento,
            }
        _append_cita(
            citas,
            seen,
            {
                "tipo": "articulo",
                "subtipo": "ley",
                "textoOriginal": texto_original,
                "inicio": match.start(),
                "fin": match.end(),
                "articulos": _extract_article_numbers(articulo_fragmento),
                "leyMencionada": ley_fragmento,
                "ley": (ley_resuelta or {}).get("nombre") or ley_fragmento,
                "leyExpandida": (abbreviation or {}).get("nombreResuelto") or "",
                "resueltaPorAbreviatura": abbreviation is not None and ley_resuelta is not None,
                "idLegislacion": (ley_resuelta or {}).get("id"),
                "categoria": (ley_resuelta or {}).get("categoria"),
                "resuelta": ley_resuelta is not None,
            },
        )

    for match in _RE_REGISTRO_DIGITAL.finditer(texto):
        _append_cita(
            citas,
            seen,
            {
                "tipo": "jurisprudencia",
                "subtipo": "registroDigital",
                "textoOriginal": match.group(0),
                "inicio": match.start(),
                "fin": match.end(),
                "registroDigital": match.group(1),
                "resuelta": True,
                "confianza": "alta",
                "requiereConfirmacion": False,
                "fuenteProbable": "SJF",
            },
        )

    for match in _RE_TESIS_AISLADA_CLAVE.finditer(texto):
        _append_cita_if_not_contained(
            citas,
            seen,
            _enrich_jurisprudencial_cita({
                "tipo": "tesis",
                "subtipo": "criterioAislado",
                "textoOriginal": match.group(0),
                "inicio": match.start(),
                "fin": match.end(),
                "clave": match.group(1).strip(),
            }),
        )

    for match in _RE_JURIS_CLAVE.finditer(texto):
        lower_text = match.group(0).lower()
        subtipo = "tesis" if ("tesis" in lower_text or "criterio aislado" in lower_text) else "jurisprudencia"
        subtype_label = "criterioAislado" if "criterio aislado" in lower_text else "clave"
        _append_cita_if_not_contained(
            citas,
            seen,
            _enrich_jurisprudencial_cita({
                "tipo": subtipo,
                "subtipo": subtype_label,
                "textoOriginal": match.group(0),
                "inicio": match.start(),
                "fin": match.end(),
                "clave": match.group(1).strip(),
            }),
        )

    for match in _RE_J_CLAVE_COMPACTA.finditer(texto):
        _append_cita_if_not_contained(
            citas,
            seen,
            _enrich_jurisprudencial_cita({
                "tipo": "jurisprudencia",
                "subtipo": "clave",
                "textoOriginal": match.group(0),
                "inicio": match.start(),
                "fin": match.end(),
                "clave": match.group(0).strip(),
            }),
        )

    citas.sort(key=lambda item: (item.get("inicio", 0), item.get("fin", 0)))
    return citas


def _sjf_search_core(sjf_payload: dict, page: int, size: int, include_raw: bool) -> Any:
    url = f"{SJF_BASE}/tesis?page={page}&size={size}"
    status, data = _http_json(url, method="POST", body=sjf_payload, headers=_sjf_headers(content_type=True), use_cache=True)
    if status >= 400:
        return JSONResponse(
            status_code=status,
            content={"error": "SJF upstream error", "status": status, "upstream": data},
        )
    docs = _extract_docs(data)
    raw_total = (data.get("total") or data.get("totalElements")) if isinstance(data, dict) else None
    raw_pages = (data.get("totalPages") or data.get("pages")) if isinstance(data, dict) else None
    total = _to_int(raw_total, len(docs))
    total_pages = _to_int(raw_pages, int((total + size - 1) / size) if size else 0)
    items = [_normalize_doc(doc, include_raw=include_raw) for doc in docs]
    return {
        "total": total,
        "totalPages": total_pages,
        "page": page,
        "size": size,
        "count": len(items),
        "hasMore": (page + 1) < total_pages,
        "items": items,
    }


@app.get("/sjf/search")
@app.get("/jurisprudencia/buscar")
def sjf_search(
    q: str = Query(default=""),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=10, ge=1, le=50),
    includeRaw: bool = Query(default=False),
):
    return _sjf_search_core(_default_sjf_payload(q), page, size, includeRaw)


def _bj_buscar_core(req_payload: dict, include_raw: bool, normalizer) -> Any:
    status, data = _http_json(
        f"{BJ_SCJN_BASE}/busqueda",
        method="POST",
        body=req_payload,
        headers=_bj_scjn_headers(content_type=True),
        use_cache=True,
    )
    if status >= 400:
        return JSONResponse(
            status_code=status,
            content={"error": "SCJN buscador request failed", "status": status, "upstream": data},
        )
    size = req_payload["size"]
    page = req_payload["page"]
    resultados = _extract_bj_items(data)
    total = _to_int(data.get("total") if isinstance(data, dict) else None, len(resultados))
    total_paginas = _to_int(
        data.get("totalPaginas") if isinstance(data, dict) else None,
        int((total + size - 1) / size) if size else 0,
    )
    items = [normalizer(item, include_raw=include_raw) for item in resultados]
    return {
        "query": req_payload["q"],
        "fuente": req_payload["fuente"],
        "indice": req_payload["indice"],
        "semantica": req_payload["semantica"],
        "total": total,
        "totalPages": total_paginas,
        "page": page,
        "size": size,
        "count": len(items),
        "hasMore": page < total_paginas,
        "items": items,
    }


def _precedentes_buscar_core(req_payload: dict, include_raw: bool) -> Any:
    return _bj_buscar_core(req_payload, include_raw, _normalize_bj_item)


def _legislacion_buscar_core(req_payload: dict, include_raw: bool) -> Any:
    return _bj_buscar_core(req_payload, include_raw, _normalize_bj_legislacion_item)


def _legislacion_detalle_core(documento_id: int, include_raw: bool) -> Any:
    status, data = _http_json(
        f"{BJ_SCJN_BASE}/documento/legislacion/{documento_id}",
        method="GET",
        headers=_bj_scjn_headers(content_type=False),
        use_cache=True,
    )
    if status >= 400:
        return JSONResponse(
            status_code=status,
            content={"error": "SCJN legislacion detail failed", "status": status, "upstream": data},
        )
    if not isinstance(data, dict):
        return JSONResponse(status_code=502, content={"error": "Unexpected legislation detail response"})
    return _normalize_bj_legislacion_detail(data, documento_id, include_raw=include_raw)


def _is_legislacion_articulo(bloque: dict) -> bool:
    referencia = _normalize_text(bloque.get("referencia") or "")
    contenido = _normalize_text(bloque.get("contenidoPlano") or bloque.get("contenido") or "")
    return bool(bloque.get("numero") is not None or referencia.startswith("articulo") or contenido.startswith("articulo "))


def _legislacion_articulos_buscar_core(
    documento_id: int,
    articulo: Optional[str],
    q: Optional[str],
    include_raw: bool,
) -> Any:
    detail = _legislacion_detalle_core(documento_id, include_raw)
    if isinstance(detail, JSONResponse):
        return detail

    bloques = detail.get("bloques") or []
    articulos = [bloque for bloque in bloques if isinstance(bloque, dict) and _is_legislacion_articulo(bloque)]

    articulo_norm = _normalize_search_text(articulo or "")
    q_norm = _normalize_search_text(q or "")

    def matches(bloque: dict) -> bool:
        numero = str(bloque.get("numero") or "").strip()
        referencia = _normalize_search_text(bloque.get("referencia") or "")
        contenido = _normalize_search_text(bloque.get("contenidoPlano") or bloque.get("contenido") or "")

        if articulo_norm:
            articulo_token = articulo_norm.replace("articulo", "").strip()
            numero_ok = bool(numero and articulo_token and numero == articulo_token)
            referencia_ok = bool(articulo_token and re.search(rf"\barticulo\s+{re.escape(articulo_token)}\b", referencia))
            if not numero_ok and not referencia_ok:
                return False

        if q_norm and q_norm not in contenido and q_norm not in referencia:
            return False

        return True

    items = [bloque for bloque in articulos if matches(bloque)]
    return {
        "id": detail.get("id"),
        "ordenamiento": detail.get("ordenamiento"),
        "articulo": articulo or "",
        "query": q or "",
        "totalArticulos": len(articulos),
        "count": len(items),
        "items": items,
    }


def _build_bj_legislacion_filters(
    categoria_ordenamiento: Optional[str] = None,
    ambito: Optional[str] = None,
    estado: Optional[str] = None,
    materia: Optional[str] = None,
    vigencia: Optional[str] = None,
) -> dict:
    filtros = {}
    filter_map = {
        "categoriaOrdenamiento": categoria_ordenamiento,
        "ambito": ambito,
        "estado": estado,
        "materia": materia,
        "vigencia": vigencia,
    }
    for key, raw_value in filter_map.items():
        if raw_value is None:
            continue
        values = [value.strip() for value in str(raw_value).split(",") if value.strip()]
        if values:
            filtros[key] = values
    return filtros


def _build_bj_legislacion_payload(
    q: str,
    page: int,
    size: int,
    fuente: str = "SIL",
    indice: str = "legislacion",
    extractos: int = 200,
    semantica: int = 0,
    filtros: Optional[dict] = None,
    sort_field: str = "",
    sort_direccion: str = "",
) -> dict:
    return {
        "q": str(q or ""),
        "page": max(1, _to_int(page, 1)),
        "size": min(50, max(1, _to_int(size, 10))),
        "indice": str(indice or "legislacion"),
        "fuente": str(fuente or "SIL"),
        "extractos": min(1000, max(0, _to_int(extractos, 200))),
        "semantica": 1 if _to_int(semantica, 0) == 1 else 0,
        "filtros": filtros if isinstance(filtros, dict) else {},
        "sortField": str(sort_field or ""),
        "sortDireccion": str(sort_direccion or ""),
    }


def _buscar_ley_core(id: Optional[int] = None, categoria: Optional[int] = None, nombre: Optional[str] = None) -> list[dict]:
    resultados = leyes
    if id is not None:
        resultados = [l for l in resultados if l["id"] == id]
    if categoria is not None:
        resultados = [l for l in resultados if l["categoria"] == categoria]
    if nombre is not None:
        nombre_norm = _normalize_text(nombre)
        tokens = [token for token in nombre_norm.split(" ") if token]

        def matches(item):
            item_norm = _normalize_text(item.get("nombre", ""))
            if not item_norm:
                return False
            if nombre_norm and nombre_norm in item_norm:
                return True
            return bool(tokens) and all(token in item_norm for token in tokens)

        resultados = [l for l in resultados if matches(l)]
    return resultados


def _score_norma_match(query_norm: str, candidate_norm: str) -> int:
    if not query_norm or not candidate_norm:
        return 0
    if query_norm == candidate_norm:
        return 1000
    score = 0
    if query_norm in candidate_norm:
        score += 500
    if candidate_norm in query_norm:
        score += 300
    tokens = [token for token in query_norm.split(" ") if token]
    score += sum(25 for token in tokens if token in candidate_norm)
    score -= abs(len(candidate_norm) - len(query_norm))
    return score


def _resolve_local_for_sil_item(sil_item: dict) -> Optional[dict]:
    categoria = _normalize_text(sil_item.get("categoriaOrdenamiento") or "")
    if categoria and categoria not in {"ley", "constitucion", "codigo", "reglamento", "estatuto"}:
        return None

    ordenamiento = sil_item.get("ordenamiento") or ""
    ordenamiento_norm = _normalize_text(ordenamiento)
    local_match = _resolve_ley_reference(ordenamiento)
    if local_match is None:
        return None

    local_norm = _normalize_text(local_match.get("nombre") or "")
    if not local_norm:
        return None
    if ordenamiento_norm == local_norm:
        return local_match
    if ordenamiento_norm.startswith(local_norm):
        extra = ordenamiento_norm[len(local_norm):].strip(" ,.-")
        if not extra or extra.startswith("(abrogada"):
            return local_match
    return None


def _merge_norma_sources(local_results: list[dict], sil_items: list[dict], query: str) -> list[dict]:
    query_norm = _normalize_text(query)
    merged = []
    seen_local_ids = set()
    seen_sil_ids = set()

    for sil_item in sil_items:
        ordenamiento = sil_item.get("ordenamiento") or ""
        local_match = _resolve_local_for_sil_item(sil_item)
        if local_match is not None:
            seen_local_ids.add(local_match.get("id"))
            seen_sil_ids.add(sil_item.get("id"))
            merged.append(
                {
                    "nombre": local_match.get("nombre") or ordenamiento,
                    "nombreNormalizado": _normalize_text(local_match.get("nombre") or ordenamiento),
                    "rutaSugerida": "jurislex",
                    "disponibleEn": ["jurislex", "sil"],
                    "jurislex": {
                        "idLegislacion": local_match.get("id"),
                        "categoria": local_match.get("categoria"),
                        "nombre": local_match.get("nombre") or "",
                    },
                    "sil": {
                        "id": sil_item.get("id"),
                        "ordenamiento": ordenamiento,
                        "categoriaOrdenamiento": sil_item.get("categoriaOrdenamiento") or "",
                        "ambito": sil_item.get("ambito") or "",
                        "estado": sil_item.get("estado") or "",
                        "vigencia": sil_item.get("vigencia") or "",
                    },
                    "score": max(
                        _score_norma_match(query_norm, _normalize_text(local_match.get("nombre") or "")),
                        _score_norma_match(query_norm, _normalize_text(ordenamiento)),
                    ),
                }
            )

    for local_item in local_results:
        if local_item.get("id") in seen_local_ids:
            continue
        merged.append(
            {
                "nombre": local_item.get("nombre") or "",
                "nombreNormalizado": _normalize_text(local_item.get("nombre") or ""),
                "rutaSugerida": "jurislex",
                "disponibleEn": ["jurislex"],
                "jurislex": {
                    "idLegislacion": local_item.get("id"),
                    "categoria": local_item.get("categoria"),
                    "nombre": local_item.get("nombre") or "",
                },
                "sil": None,
                "score": _score_norma_match(query_norm, _normalize_text(local_item.get("nombre") or "")),
            }
        )

    for sil_item in sil_items:
        if sil_item.get("id") in seen_sil_ids:
            continue
        ordenamiento = sil_item.get("ordenamiento") or ""
        merged.append(
            {
                "nombre": ordenamiento,
                "nombreNormalizado": _normalize_text(ordenamiento),
                "rutaSugerida": "sil",
                "disponibleEn": ["sil"],
                "jurislex": None,
                "sil": {
                    "id": sil_item.get("id"),
                    "ordenamiento": ordenamiento,
                    "categoriaOrdenamiento": sil_item.get("categoriaOrdenamiento") or "",
                    "ambito": sil_item.get("ambito") or "",
                    "estado": sil_item.get("estado") or "",
                    "vigencia": sil_item.get("vigencia") or "",
                },
                "score": _score_norma_match(query_norm, _normalize_text(ordenamiento)),
            }
        )

    merged.sort(key=lambda item: (item.get("score", 0), item.get("nombre", "")), reverse=True)
    for item in merged:
        item.pop("score", None)
        item.pop("nombreNormalizado", None)
    return merged


def _normas_buscar_core(
    nombre: str,
    page: int,
    size: int,
    categoria_ordenamiento: Optional[str] = None,
    ambito: Optional[str] = None,
    estado: Optional[str] = None,
    materia: Optional[str] = None,
    vigencia: Optional[str] = None,
    semantica: int = 0,
    include_raw: bool = False,
) -> Any:
    sil_only_filters_active = any([categoria_ordenamiento, ambito, estado, materia, vigencia])
    local_results = [] if sil_only_filters_active else _buscar_ley_core(nombre=nombre)
    sil_payload = _build_bj_legislacion_payload(
        q=nombre,
        page=page,
        size=max(size, 50),
        semantica=semantica,
        filtros=_build_bj_legislacion_filters(
            categoria_ordenamiento=categoria_ordenamiento,
            ambito=ambito,
            estado=estado,
            materia=materia,
            vigencia=vigencia,
        ),
    )
    sil_response = _legislacion_buscar_core(sil_payload, include_raw)
    if isinstance(sil_response, JSONResponse):
        return sil_response
    sil_items = sil_response.get("items") or []
    merged_items = _merge_norma_sources(local_results, sil_items, nombre)
    total = len(merged_items)
    total_pages = max(1, int((total + size - 1) / size)) if size else 1
    start = max(0, (page - 1) * size)
    end = start + size
    paged_items = merged_items[start:end]

    return {
        "query": nombre,
        "page": page,
        "size": size,
        "count": len(paged_items),
        "total": total,
        "totalPages": total_pages,
        "hasMore": page < total_pages,
        "jurislexCount": len(local_results),
        "silCount": len(sil_items),
        "items": paged_items,
    }


def _extract_articulo_numero(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    try:
        return int(match.group(0))
    except Exception:
        return None


def _normas_articulos_buscar_core(
    nombre: str,
    articulo: Optional[str],
    q: Optional[str],
    page: int,
    size: int,
    categoria_ordenamiento: Optional[str] = None,
    ambito: Optional[str] = None,
    estado: Optional[str] = None,
    materia: Optional[str] = None,
    vigencia: Optional[str] = None,
    semantica: int = 0,
    include_raw: bool = False,
) -> Any:
    normas = _normas_buscar_core(
        nombre=nombre,
        page=page,
        size=size,
        categoria_ordenamiento=categoria_ordenamiento,
        ambito=ambito,
        estado=estado,
        materia=materia,
        vigencia=vigencia,
        semantica=semantica,
        include_raw=include_raw,
    )
    if isinstance(normas, JSONResponse):
        return normas

    items = normas.get("items") or []
    if not items:
        return {
            "nombre": nombre,
            "articulo": articulo or "",
            "query": q or "",
            "fuenteUsada": "",
            "fuentesIntentadas": [],
            "norma": None,
            "count": 0,
            "items": [],
        }

    norma = items[0]
    articulo_numero = _extract_articulo_numero(articulo)
    fuentes_intentadas = []

    def try_jurislex() -> Optional[dict]:
        if not norma.get("jurislex"):
            return None
        ref = norma["jurislex"]
        response = jurislex_buscar_articulos(
            categoria=int(ref["categoria"]),
            idLegislacion=int(ref["idLegislacion"]),
            desc=str(q or ""),
            soloArticulo=True,
            indice=0,
            elementos=size,
            articuloNumero=articulo_numero,
            includeRaw=include_raw,
        )
        if isinstance(response, JSONResponse):
            return None
        return {
            "fuenteUsada": "jurislex",
            "fuentesIntentadas": list(fuentes_intentadas),
            "norma": norma,
            "count": response.get("count", 0),
            "items": response.get("items") or [],
            "upstream": response,
        }

    def try_sil() -> Optional[dict]:
        if not norma.get("sil"):
            return None
        ref = norma["sil"]
        response = _legislacion_articulos_buscar_core(
            documento_id=int(ref["id"]),
            articulo=articulo,
            q=q,
            include_raw=include_raw,
        )
        if isinstance(response, JSONResponse):
            return None
        return {
            "fuenteUsada": "sil",
            "fuentesIntentadas": list(fuentes_intentadas),
            "norma": norma,
            "count": response.get("count", 0),
            "items": response.get("items") or [],
            "upstream": response,
        }

    ordered_sources = [norma.get("rutaSugerida")] + [source for source in ["jurislex", "sil"] if source != norma.get("rutaSugerida")]
    for source in ordered_sources:
        if source == "jurislex":
            fuentes_intentadas.append("jurislex")
            result = try_jurislex()
        elif source == "sil":
            fuentes_intentadas.append("sil")
            result = try_sil()
        else:
            continue
        if result and result.get("count", 0) > 0:
            return {
                "nombre": nombre,
                "articulo": articulo or "",
                "query": q or "",
                **result,
            }

    return {
        "nombre": nombre,
        "articulo": articulo or "",
        "query": q or "",
        "fuenteUsada": "",
        "fuentesIntentadas": fuentes_intentadas,
        "norma": norma,
        "count": 0,
        "items": [],
    }


def _clean_optional_text(value: Any) -> str:
    text = _strip_html(value or "")
    return text.strip()


def _sil_article_structure(detail: dict, article_id: Optional[int], article_numero: Optional[int]) -> dict:
    bloques = detail.get("bloques") or []
    target_index = None
    for index, bloque in enumerate(bloques):
        if article_id is not None and bloque.get("id") == article_id:
            target_index = index
            break
    if target_index is None and article_numero is not None:
        for index, bloque in enumerate(bloques):
            if bloque.get("numero") == article_numero:
                target_index = index
                break

    structure = {"libro": "", "titulo": "", "capitulo": ""}
    if target_index is None:
        return structure

    for bloque in reversed(bloques[:target_index]):
        referencia = _normalize_text(bloque.get("referencia") or "")
        contenido = _clean_optional_text(bloque.get("contenidoPlano") or bloque.get("contenido") or "")
        if not structure["capitulo"] and referencia.startswith("capitulo"):
            structure["capitulo"] = contenido
        elif not structure["titulo"] and referencia.startswith("titulo"):
            structure["titulo"] = contenido
        elif not structure["libro"] and referencia.startswith("libro"):
            structure["libro"] = contenido
        if all(structure.values()):
            break
    return structure


def _normas_articulos_detalle_core(
    nombre: str,
    articulo: Optional[str],
    q: Optional[str],
    page: int,
    size: int,
    categoria_ordenamiento: Optional[str] = None,
    ambito: Optional[str] = None,
    estado: Optional[str] = None,
    materia: Optional[str] = None,
    vigencia: Optional[str] = None,
    semantica: int = 0,
    include_raw: bool = False,
) -> Any:
    search = _normas_articulos_buscar_core(
        nombre=nombre,
        articulo=articulo,
        q=q,
        page=page,
        size=size,
        categoria_ordenamiento=categoria_ordenamiento,
        ambito=ambito,
        estado=estado,
        materia=materia,
        vigencia=vigencia,
        semantica=semantica,
        include_raw=include_raw,
    )
    if isinstance(search, JSONResponse):
        return search

    items = search.get("items") or []
    if not items:
        return JSONResponse(status_code=404, content={"error": "articulo no encontrado"})

    selected = items[0]
    fuente = search.get("fuenteUsada") or ""
    norma = search.get("norma") or {}

    if fuente == "jurislex":
        ref = norma.get("jurislex") or {}
        detail = jurislex_detalle_articulo(
            categoria=int(ref.get("categoria")),
            idLegislacion=int(ref.get("idLegislacion")),
            idArticulo=int(selected.get("idArticulo")),
            includeRaw=include_raw,
        )
        if isinstance(detail, JSONResponse):
            return detail
        return {
            "nombre": nombre,
            "articuloSolicitado": articulo or "",
            "query": q or "",
            "fuenteUsada": "jurislex",
            "norma": norma,
            "articulo": {
                "numero": selected.get("numeroArticulo"),
                "referencia": f"Artículo {selected.get('numeroArticulo')}" if selected.get("numeroArticulo") is not None else "",
                "ley": detail.get("ley") or selected.get("ley") or "",
                "libro": _clean_optional_text(detail.get("libro") or ((detail.get("raw") or {}).get("sLibro") if include_raw else "")),
                "titulo": _clean_optional_text(detail.get("titulo") or ((detail.get("raw") or {}).get("sTitulo") if include_raw else "")),
                "capitulo": _clean_optional_text(detail.get("capitulo") or ((detail.get("raw") or {}).get("sCapitulo") if include_raw else "")),
                "texto": _clean_optional_text(detail.get("texto") or selected.get("texto") or ""),
                "textoPlano": detail.get("textoPlano") or selected.get("textoPlano") or "",
                "meta": {
                    "idArticulo": detail.get("idArticulo") or selected.get("idArticulo"),
                    "idLegislacion": detail.get("idLegislacion") or selected.get("idLegislacion"),
                    "categoria": detail.get("categoria") or ref.get("categoria"),
                },
            },
            "coincidencias": search.get("count", 0),
            "fuentesIntentadas": search.get("fuentesIntentadas") or [],
            "rawSearch": search.get("upstream") if include_raw else None,
        }

    if fuente == "sil":
        sil_ref = norma.get("sil") or {}
        detail = _legislacion_detalle_core(int(sil_ref.get("id")), include_raw)
        if isinstance(detail, JSONResponse):
            return detail
        structure = _sil_article_structure(detail, selected.get("id"), selected.get("numero"))
        return {
            "nombre": nombre,
            "articuloSolicitado": articulo or "",
            "query": q or "",
            "fuenteUsada": "sil",
            "norma": norma,
            "articulo": {
                "numero": selected.get("numero"),
                "referencia": selected.get("referencia") or "",
                "ley": detail.get("ordenamiento") or norma.get("nombre") or "",
                "libro": structure.get("libro") or "",
                "titulo": structure.get("titulo") or "",
                "capitulo": structure.get("capitulo") or "",
                "texto": selected.get("contenido") or "",
                "textoPlano": selected.get("contenidoPlano") or "",
                "meta": {
                    "id": selected.get("id"),
                    "documentoId": detail.get("id") or sil_ref.get("id"),
                    "orden": selected.get("orden"),
                    "vigencia": selected.get("vigencia") or "",
                    "fechaActualizacion": selected.get("fechaActualizacion") or "",
                },
            },
            "coincidencias": search.get("count", 0),
            "fuentesIntentadas": search.get("fuentesIntentadas") or [],
            "rawSearch": search.get("upstream") if include_raw else None,
        }

    return JSONResponse(status_code=404, content={"error": "articulo no encontrado"})


@app.get("/precedentes/buscar")
@app.get("/scjn/precedentes/buscar")
def scjn_precedentes_buscar(
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
    indice: str = Query(default="ejecutorias"),
    fuente: str = Query(default="SJF"),
    extractos: int = Query(default=200, ge=0, le=1000),
    semantica: int = Query(default=0, ge=0, le=1),
    includeRaw: bool = Query(default=False),
):
    req_payload = {
        "q": str(q or ""),
        "page": page,
        "size": size,
        "indice": str(indice or "ejecutorias"),
        "fuente": str(fuente or "SJF"),
        "extractos": extractos,
        "semantica": semantica,
        "filtros": {},
        "sortField": "",
        "sortDireccion": "",
    }
    return _precedentes_buscar_core(req_payload, includeRaw)


@app.post("/precedentes/buscar")
@app.post("/scjn/precedentes/buscar")
def scjn_precedentes_buscar_post(
    includeRaw: bool = Query(default=False),
    payload: dict = Body(default={}),
):
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"error": "Invalid payload"})

    req_payload = {
        "q": str(payload.get("q") or ""),
        "page": max(1, _to_int(payload.get("page"), 1)),
        "size": min(50, max(1, _to_int(payload.get("size"), 10))),
        "indice": str(payload.get("indice") or "ejecutorias"),
        "fuente": str(payload.get("fuente") or "SJF"),
        "extractos": min(1000, max(0, _to_int(payload.get("extractos"), 200))),
        "semantica": 1 if _to_int(payload.get("semantica"), 0) == 1 else 0,
        "filtros": payload.get("filtros") if isinstance(payload.get("filtros"), dict) else {},
        "sortField": str(payload.get("sortField") or ""),
        "sortDireccion": str(payload.get("sortDireccion") or ""),
    }
    return _precedentes_buscar_core(req_payload, _to_bool(payload.get("includeRaw"), includeRaw))


@app.get("/legislacion/buscar")
@app.get("/scjn/legislacion/buscar")
def scjn_legislacion_buscar(
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
    fuente: str = Query(default="SIL"),
    indice: str = Query(default="legislacion"),
    extractos: int = Query(default=200, ge=0, le=1000),
    semantica: int = Query(default=0, ge=0, le=1),
    categoriaOrdenamiento: Optional[str] = Query(default=None),
    ambito: Optional[str] = Query(default=None),
    estado: Optional[str] = Query(default=None),
    materia: Optional[str] = Query(default=None),
    vigencia: Optional[str] = Query(default=None),
    includeRaw: bool = Query(default=False),
):
    req_payload = _build_bj_legislacion_payload(
        q=q,
        page=page,
        size=size,
        fuente=fuente,
        indice=indice,
        extractos=extractos,
        semantica=semantica,
        filtros=_build_bj_legislacion_filters(
            categoria_ordenamiento=categoriaOrdenamiento,
            ambito=ambito,
            estado=estado,
            materia=materia,
            vigencia=vigencia,
        ),
    )
    return _legislacion_buscar_core(req_payload, includeRaw)


@app.post("/legislacion/buscar")
@app.post("/scjn/legislacion/buscar")
def scjn_legislacion_buscar_post(
    includeRaw: bool = Query(default=False),
    payload: dict = Body(default={}),
):
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"error": "Invalid payload"})

    req_payload = _build_bj_legislacion_payload(
        q=str(payload.get("q") or ""),
        page=_to_int(payload.get("page"), 1),
        size=_to_int(payload.get("size"), 10),
        fuente=str(payload.get("fuente") or "SIL"),
        indice=str(payload.get("indice") or "legislacion"),
        extractos=_to_int(payload.get("extractos"), 200),
        semantica=_to_int(payload.get("semantica"), 0),
        filtros=payload.get("filtros") if isinstance(payload.get("filtros"), dict) else {},
        sort_field=str(payload.get("sortField") or ""),
        sort_direccion=str(payload.get("sortDireccion") or ""),
    )
    return _legislacion_buscar_core(req_payload, _to_bool(payload.get("includeRaw"), includeRaw))


@app.get("/normas/buscar")
@app.get("/legislacion/unificada/buscar")
def normas_buscar(
    nombre: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
    categoriaOrdenamiento: Optional[str] = Query(default=None),
    ambito: Optional[str] = Query(default=None),
    estado: Optional[str] = Query(default=None),
    materia: Optional[str] = Query(default=None),
    vigencia: Optional[str] = Query(default=None),
    semantica: int = Query(default=0, ge=0, le=1),
    includeRaw: bool = Query(default=False),
):
    if not str(nombre or "").strip():
        return JSONResponse(status_code=400, content={"error": "nombre es requerido"})
    return _normas_buscar_core(
        nombre=str(nombre or ""),
        page=page,
        size=size,
        categoria_ordenamiento=categoriaOrdenamiento,
        ambito=ambito,
        estado=estado,
        materia=materia,
        vigencia=vigencia,
        semantica=semantica,
        include_raw=includeRaw,
    )


@app.post("/normas/buscar")
@app.post("/legislacion/unificada/buscar")
def normas_buscar_post(
    includeRaw: bool = Query(default=False),
    payload: dict = Body(default={}),
):
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"error": "Invalid payload"})
    nombre = str(payload.get("nombre") or payload.get("q") or "")
    if not nombre.strip():
        return JSONResponse(status_code=400, content={"error": "nombre es requerido"})
    return _normas_buscar_core(
        nombre=nombre,
        page=max(1, _to_int(payload.get("page"), 1)),
        size=min(50, max(1, _to_int(payload.get("size"), 10))),
        categoria_ordenamiento=str(payload.get("categoriaOrdenamiento") or "") or None,
        ambito=str(payload.get("ambito") or "") or None,
        estado=str(payload.get("estado") or "") or None,
        materia=str(payload.get("materia") or "") or None,
        vigencia=str(payload.get("vigencia") or "") or None,
        semantica=_to_int(payload.get("semantica"), 0),
        include_raw=_to_bool(payload.get("includeRaw"), includeRaw),
    )


@app.get("/normas/articulos/buscar")
def normas_articulos_buscar(
    nombre: str = Query(default=""),
    articulo: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
    categoriaOrdenamiento: Optional[str] = Query(default=None),
    ambito: Optional[str] = Query(default=None),
    estado: Optional[str] = Query(default=None),
    materia: Optional[str] = Query(default=None),
    vigencia: Optional[str] = Query(default=None),
    semantica: int = Query(default=0, ge=0, le=1),
    includeRaw: bool = Query(default=False),
):
    if not str(nombre or "").strip():
        return JSONResponse(status_code=400, content={"error": "nombre es requerido"})
    return _normas_articulos_buscar_core(
        nombre=str(nombre or ""),
        articulo=articulo,
        q=q,
        page=page,
        size=size,
        categoria_ordenamiento=categoriaOrdenamiento,
        ambito=ambito,
        estado=estado,
        materia=materia,
        vigencia=vigencia,
        semantica=semantica,
        include_raw=includeRaw,
    )


@app.post("/normas/articulos/buscar")
def normas_articulos_buscar_post(
    includeRaw: bool = Query(default=False),
    payload: dict = Body(default={}),
):
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"error": "Invalid payload"})
    nombre = str(payload.get("nombre") or payload.get("qNorma") or "")
    if not nombre.strip():
        return JSONResponse(status_code=400, content={"error": "nombre es requerido"})
    return _normas_articulos_buscar_core(
        nombre=nombre,
        articulo=str(payload.get("articulo") or "") or None,
        q=str(payload.get("q") or "") or None,
        page=max(1, _to_int(payload.get("page"), 1)),
        size=min(50, max(1, _to_int(payload.get("size"), 10))),
        categoria_ordenamiento=str(payload.get("categoriaOrdenamiento") or "") or None,
        ambito=str(payload.get("ambito") or "") or None,
        estado=str(payload.get("estado") or "") or None,
        materia=str(payload.get("materia") or "") or None,
        vigencia=str(payload.get("vigencia") or "") or None,
        semantica=_to_int(payload.get("semantica"), 0),
        include_raw=_to_bool(payload.get("includeRaw"), includeRaw),
    )


@app.get("/normas/articulos/detalle")
def normas_articulos_detalle(
    nombre: str = Query(default=""),
    articulo: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
    categoriaOrdenamiento: Optional[str] = Query(default=None),
    ambito: Optional[str] = Query(default=None),
    estado: Optional[str] = Query(default=None),
    materia: Optional[str] = Query(default=None),
    vigencia: Optional[str] = Query(default=None),
    semantica: int = Query(default=0, ge=0, le=1),
    includeRaw: bool = Query(default=False),
):
    if not str(nombre or "").strip():
        return JSONResponse(status_code=400, content={"error": "nombre es requerido"})
    return _normas_articulos_detalle_core(
        nombre=str(nombre or ""),
        articulo=articulo,
        q=q,
        page=page,
        size=size,
        categoria_ordenamiento=categoriaOrdenamiento,
        ambito=ambito,
        estado=estado,
        materia=materia,
        vigencia=vigencia,
        semantica=semantica,
        include_raw=includeRaw,
    )


@app.post("/normas/articulos/detalle")
def normas_articulos_detalle_post(
    includeRaw: bool = Query(default=False),
    payload: dict = Body(default={}),
):
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"error": "Invalid payload"})
    nombre = str(payload.get("nombre") or payload.get("qNorma") or "")
    if not nombre.strip():
        return JSONResponse(status_code=400, content={"error": "nombre es requerido"})
    return _normas_articulos_detalle_core(
        nombre=nombre,
        articulo=str(payload.get("articulo") or "") or None,
        q=str(payload.get("q") or "") or None,
        page=max(1, _to_int(payload.get("page"), 1)),
        size=min(50, max(1, _to_int(payload.get("size"), 10))),
        categoria_ordenamiento=str(payload.get("categoriaOrdenamiento") or "") or None,
        ambito=str(payload.get("ambito") or "") or None,
        estado=str(payload.get("estado") or "") or None,
        materia=str(payload.get("materia") or "") or None,
        vigencia=str(payload.get("vigencia") or "") or None,
        semantica=_to_int(payload.get("semantica"), 0),
        include_raw=_to_bool(payload.get("includeRaw"), includeRaw),
    )


@app.get("/legislacion/detalle")
@app.get("/scjn/legislacion/detalle")
def scjn_legislacion_detalle(
    id: int = Query(..., gt=0),
    includeRaw: bool = Query(default=False),
):
    return _legislacion_detalle_core(id, includeRaw)


@app.get("/legislacion/articulos/buscar")
@app.get("/scjn/legislacion/articulos/buscar")
def scjn_legislacion_articulos_buscar(
    id: int = Query(..., gt=0),
    articulo: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    includeRaw: bool = Query(default=False),
):
    return _legislacion_articulos_buscar_core(id, articulo, q, includeRaw)


@app.post("/sjf/search")
@app.post("/jurisprudencia/buscar")
def sjf_search_advanced(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=10, ge=1, le=50),
    includeRaw: bool = Query(default=False),
    payload: dict = Body(default={}),
):
    if not isinstance(payload, dict) or not payload:
        return JSONResponse(status_code=400, content={"error": "Invalid or empty payload"})
    return _sjf_search_core(payload, page, size, includeRaw)


@app.get("/sjf/detail")
@app.get("/jurisprudencia/detalle")
def sjf_detail(
    ius: int = Query(..., gt=0),
    isSemanal: Optional[bool] = Query(default=None),
    hostName: Optional[str] = Query(default="https://sjf2.scjn.gob.mx"),
    includeRaw: Optional[bool] = Query(default=False),
    debug: bool = Query(default=False),
):
    hostName = hostName or "https://sjf2.scjn.gob.mx"
    includeRaw = bool(includeRaw)
    debug = bool(debug)

    result, attempts = _sjf_detail_attempts(ius, hostName, isSemanal)
    status = result["status"]
    data = result["data"]
    used = result["isSemanal"]

    if status >= 400:
        error_content = {
            "error": "SJF detail request failed",
            "status": status,
            "upstream": data,
        }
        if debug:
            error_content["debug"] = {
                "attempts": [
                    {
                        "status": attempt["status"],
                        "url": attempt["url"],
                        "isSemanal": attempt["isSemanal"],
                        "hostNameIncluded": attempt["hostNameIncluded"],
                        "durationMs": attempt["durationMs"],
                        "requestHeaders": attempt["requestHeaders"],
                        "upstream": attempt["data"],
                    }
                    for attempt in attempts
                ]
            }
        return JSONResponse(
            status_code=status,
            content=error_content,
        )

    texto = ""
    if isinstance(data, dict):
        texto = str(data.get("texto") or data.get("textoPublicacion") or data.get("contenido") or "")
    elif isinstance(data, str):
        texto = data

    rubro_raw = data.get("rubro") if isinstance(data, dict) else ""
    titulo_raw = (data.get("titulo") or data.get("title")) if isinstance(data, dict) else ""

    response = {
        "ius": ius,
        "isSemanalUsed": used is True,
        "hostName": hostName,
        "rubro": _strip_html(rubro_raw).upper(),
        "fechaPublicacion": str(data.get("fechaPublicacion") if isinstance(data, dict) else ""),
        "titulo": "" if titulo_raw in (None, "None") else str(titulo_raw),
        "texto": texto,
        "textoPlano": _strip_html(texto),
    }
    if includeRaw:
        response["raw"] = data
    if debug:
        response["debug"] = {
            "attempts": [
                {
                    "status": attempt["status"],
                    "url": attempt["url"],
                    "isSemanal": attempt["isSemanal"],
                    "hostNameIncluded": attempt["hostNameIncluded"],
                    "durationMs": attempt["durationMs"],
                }
                for attempt in attempts
            ]
        }
    return response


@app.get("/jurislex/decretos")
def jurislex_decretos(
    idLegislacion: int = Query(..., gt=0),
    idOrdenamiento: Optional[int] = Query(default=None, gt=0),
):
    ordenamiento = idOrdenamiento if idOrdenamiento is not None else idLegislacion
    url = (
        f"{JURISLEX_BASE}/decrees/{ordenamiento}?"
        f"idLegis={idLegislacion}&idOrdenamiento={ordenamiento}"
    )
    status, data = _http_json(url, method="GET", headers=_jurislex_headers(content_type=False))
    if status >= 400:
        return JSONResponse(
            status_code=status,
            content={"error": "Jurislex decrees request failed", "status": status, "upstream": data},
        )
    return {"count": len(data) if isinstance(data, list) else 0, "items": data}


@app.get("/jurislex/articulos/buscar")
def jurislex_buscar_articulos(
    categoria: int = Query(..., gt=0, description="Categoria Jurislex"),
    idLegislacion: int = Query(..., gt=0, description="IdLegislacion validado"),
    desc: str = Query(default="", description="Numero de articulo o palabra"),
    soloArticulo: bool = Query(default=False),
    indice: int = Query(default=0, ge=0),
    elementos: int = Query(default=20, ge=1, le=50),
    articuloNumero: Optional[int] = Query(default=None, gt=0, description="Numero base para filtro exacto"),
    includeRaw: bool = Query(default=False),
):
    desc_value = _jurislex_desc_value(desc, articuloNumero)
    payload = {
        "datosArticulo": {
            "Indice": indice,
            "Elementos": elementos,
            "Ordenamiento": "A desc",
            "IdLegislacion": [int(idLegislacion)],
            "SoloArticulo": _to_bool(soloArticulo, False),
            "Desc": desc_value,
            "SoloIndices": False,
            "filterRaw": _jurislex_search_filter_raw(int(idLegislacion), articuloNumero, desc),
            "BusquedaGeneralArticulo": None,
            "bClipboard": False,
        }
    }

    url = f"{JURISLEX_BASE}/ObtenerArticulos/{categoria}"
    status, data = _http_json(url, method="POST", body=payload, headers=_jurislex_headers(content_type=True))
    if status >= 400:
        return JSONResponse(
            status_code=status,
            content={"error": "Jurislex buscar articulos failed", "status": status, "upstream": data},
        )

    resultado = data.get("Resultado") if isinstance(data, dict) else []
    if not isinstance(resultado, list):
        resultado = []
    items = [_normalize_jurislex_result(item, include_raw=includeRaw) for item in resultado]

    return {
        "categoria": categoria,
        "idLegislacion": idLegislacion,
        "indice": indice,
        "elementos": elementos,
        "count": len(items),
        "total": _to_int(data.get("Total") if isinstance(data, dict) else None, len(items)),
        "totalArticulos": _to_int(
            data.get("TotalArticulos") if isinstance(data, dict) else None, len(items)
        ),
        "items": items,
    }


@app.post("/jurislex/articulos/buscar")
def jurislex_buscar_articulos_post(payload: dict = Body(default={})):
    categoria = _to_int(payload.get("categoria"), None)
    datos = payload.get("datosArticulo") if isinstance(payload, dict) else None
    if categoria is None or not isinstance(datos, dict):
        return JSONResponse(status_code=400, content={"error": "categoria and datosArticulo are required"})

    datos_articulo = {
        "Indice": _to_int(datos.get("Indice"), 0),
        "Elementos": _to_int(datos.get("Elementos"), 20),
        "Ordenamiento": "A desc",
        "IdLegislacion": datos.get("IdLegislacion") if isinstance(datos.get("IdLegislacion"), list) else [],
        "SoloArticulo": _to_bool(datos.get("SoloArticulo"), False),
        "Desc": str(datos.get("Desc") or ""),
        "SoloIndices": _to_bool(datos.get("SoloIndices"), False),
        "filterRaw": str(datos.get("filterRaw") or ""),
        "BusquedaGeneralArticulo": datos.get("BusquedaGeneralArticulo"),
        "bClipboard": _to_bool(datos.get("bClipboard"), False),
    }

    req_body = {"datosArticulo": datos_articulo}
    url = f"{JURISLEX_BASE}/ObtenerArticulos/{categoria}"
    status, data = _http_json(url, method="POST", body=req_body, headers=_jurislex_headers(content_type=True))
    if status >= 400:
        return JSONResponse(
            status_code=status,
            content={"error": "Jurislex buscar articulos failed", "status": status, "upstream": data},
        )

    resultado = data.get("Resultado") if isinstance(data, dict) else []
    if not isinstance(resultado, list):
        resultado = []
    include_raw = _to_bool(payload.get("includeRaw"), False)
    items = [_normalize_jurislex_result(item, include_raw=include_raw) for item in resultado]

    return {
        "categoria": categoria,
        "count": len(items),
        "total": _to_int(data.get("Total") if isinstance(data, dict) else None, len(items)),
        "totalArticulos": _to_int(
            data.get("TotalArticulos") if isinstance(data, dict) else None, len(items)
        ),
        "items": items,
    }


@app.get("/jurislex/articulos/detalle")
def jurislex_detalle_articulo(
    categoria: int = Query(..., gt=0),
    idLegislacion: int = Query(..., gt=0),
    idArticulo: int = Query(..., gt=0),
    includeRaw: bool = Query(default=False),
):
    payload = {"datosArticulo": {"IdLegislacion": int(idLegislacion), "IdArticulo": int(idArticulo)}}
    url = f"{JURISLEX_BASE}/ObtenerDetalleArticulos/{categoria}"
    status, data = _http_json(url, method="POST", body=payload, headers=_jurislex_headers(content_type=True))
    if status >= 400:
        return JSONResponse(
            status_code=status,
            content={"error": "Jurislex detalle articulo failed", "status": status, "upstream": data},
        )

    detail = data if isinstance(data, dict) else {}
    response = {
        "categoria": categoria,
        "idLegislacion": detail.get("iIdLey") or idLegislacion,
        "idArticulo": detail.get("iIdArticulo") or idArticulo,
        "ley": detail.get("sLey") or "",
        "libro": detail.get("sLibro") or "",
        "titulo": detail.get("sTitulo") or "",
        "capitulo": detail.get("sCapitulo") or "",
        "texto": detail.get("sDescArticulo") or "",
        "textoPlano": _strip_html(detail.get("sDescArticulo") or ""),
    }
    if includeRaw:
        response["raw"] = detail
    return response


@app.post("/documentos/extraer-texto")
def extraer_texto_documento(payload: dict = Body(default={})):
    file_name = str(payload.get("fileName") or "").strip()
    content_b64 = str(payload.get("contentBase64") or "")
    extension = os.path.splitext(file_name)[1].lower()

    if not file_name or not content_b64:
        return JSONResponse(status_code=400, content={"error": "fileName y contentBase64 son requeridos"})

    if extension != ".docx":
        return JSONResponse(status_code=400, content={"error": "solo se soporta .docx en este endpoint"})

    try:
        content = base64.b64decode(content_b64)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "contentBase64 invalido"})

    texto = _extract_docx_text(content)
    if not texto:
        return JSONResponse(status_code=422, content={"error": "no se pudo extraer texto del archivo .docx"})

    return {
        "fileName": file_name,
        "extension": extension,
        "texto": texto,
        "longitud": len(texto),
    }

@app.post("/citas/extraer")
def extraer_citas(payload: dict = Body(default={})):
    texto = str(payload.get("texto") or "")
    fuente = str(payload.get("fuente") or "texto")
    resolver = _to_bool(payload.get("resolver"), False)
    texto_limpio = _strip_html(texto)

    if not texto_limpio.strip():
        return JSONResponse(status_code=400, content={"error": "texto es requerido"})

    abbreviations = _extract_document_abbreviations(texto_limpio)
    citas = _extract_citas(texto_limpio, abbreviations=abbreviations)

    if resolver:
        citas = [_merge_cita_with_detalle(cita, _resolve_cita_detalle(cita)) for cita in citas]

    articulos_resueltos = [cita for cita in citas if cita.get("tipo") == "articulo" and cita.get("resuelta")]

    response = {
        "fuente": fuente,
        "textoAnalizado": texto_limpio,
        "abreviaturasDetectadas": abbreviations,
        "resumen": {
            "totalCitas": len(citas),
            "articulos": sum(1 for cita in citas if cita.get("tipo") == "articulo"),
            "jurisprudencias": sum(1 for cita in citas if cita.get("tipo") == "jurisprudencia"),
            "tesis": sum(1 for cita in citas if cita.get("tipo") == "tesis"),
            "articulosResueltos": len(articulos_resueltos),
            "requierenConfirmacion": sum(1 for cita in citas if cita.get("requiereConfirmacion")),
        },
        "items": citas,
    }
    if resolver:
        response["reporte"] = _build_citas_report(citas)
    return response


@app.get("/")
def read_root(request: Request):
    accept = str(request.headers.get("accept") or "")
    if "text/html" in accept:
        index_path = os.path.join(BASE_DIR, "index.html")
        with open(index_path, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())

    return {
        "mensaje": "API de Ordina funcionando correctamente",
        "servicio": "Ordina-engine",
        "status": "ok",
    }


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Ordina-engine"}


@app.get("/health/deep")
def deep_health_check():
    checks = []

    catalog_ok = isinstance(leyes, list) and len(leyes) > 0
    checks.append(
        {
            "name": "catalogo-legislaciones",
            "ok": catalog_ok,
            "detail": {"count": len(leyes) if isinstance(leyes, list) else 0},
        }
    )

    sjf_payload = _default_sjf_payload("amparo")
    sjf_url = f"{SJF_BASE}/tesis?page=0&size=1"
    sjf_status, sjf_data = _http_json(
        sjf_url,
        method="POST",
        body=sjf_payload,
        headers=_sjf_headers(content_type=True),
    )
    sjf_docs = _extract_docs(sjf_data)
    sjf_ok = sjf_status == 200 and len(sjf_docs) >= 1
    checks.append(
        {
            "name": "sjf-search",
            "ok": sjf_ok,
            "detail": {"status": sjf_status, "count": len(sjf_docs)},
        }
    )

    jurislex_payload = {
        "datosArticulo": {
            "Indice": 0,
            "Elementos": 1,
            "Ordenamiento": "A desc",
            "IdLegislacion": [1000],
            "SoloArticulo": True,
            "Desc": "1",
            "SoloIndices": False,
            "filterRaw": _jurislex_filter_raw(1000, 1),
            "BusquedaGeneralArticulo": None,
            "bClipboard": False,
        }
    }
    jurislex_url = f"{JURISLEX_BASE}/ObtenerArticulos/1000"
    jl_status, jl_data = _http_json(
        jurislex_url,
        method="POST",
        body=jurislex_payload,
        headers=_jurislex_headers(content_type=True),
    )
    jl_results = jl_data.get("Resultado") if isinstance(jl_data, dict) else []
    if not isinstance(jl_results, list):
        jl_results = []
    jl_ok = jl_status == 200 and isinstance(jl_data, dict)
    checks.append(
        {
            "name": "jurislex-search",
            "ok": jl_ok,
            "detail": {"status": jl_status, "count": len(jl_results)},
        }
    )

    overall_ok = all(check.get("ok") for check in checks)
    status_text = "ok" if overall_ok else "degraded"
    status_code = 200 if overall_ok else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": status_text,
            "service": "Ordina-engine",
            "checks": checks,
        },
    )

@app.get("/ley")
def buscar_ley(id: Optional[int] = None, categoria: Optional[int] = None, nombre: Optional[str] = None):
    return JSONResponse(content=_buscar_ley_core(id=id, categoria=categoria, nombre=nombre))
