from fastapi import Body, FastAPI, Query
import json
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from urllib import parse, request, error
from typing import Optional

app = FastAPI()

# Permitir cualquier origen (útil para desarrollo/testing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

with open("IdLegislaciones.json", encoding="utf-8") as f:
    leyes = json.load(f)

SJF_BASE = "https://sjf2.scjn.gob.mx/services/sjftesismicroservice/api/public"


def _parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).lower() == "true"


def _default_sjf_payload(q: str):
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


def _sjf_headers(content_type=False):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:148.0) Gecko/20100101 Firefox/148.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://sjf2.scjn.gob.mx",
        "Referer": "https://sjf2.scjn.gob.mx/listado-resultado-tesis",
    }
    if content_type:
        headers["Content-Type"] = "application/json"
    cookie = os.getenv("SJF_COOKIE")
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _http_json(url: str, method="GET", body=None, headers=None):
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = request.Request(url=url, method=method, data=data)
    for key, value in (headers or {}).items():
        req.add_header(key, value)

    try:
        with request.urlopen(req, timeout=35) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"rawText": raw}
            return resp.status, parsed
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"rawText": raw}
        return exc.code, parsed
    except Exception:
        return 502, {"error": "SJF request failed"}


def _extract_docs(payload):
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("documents"), list):
        return payload["documents"]
    if isinstance(payload.get("content"), list):
        return payload["content"]
    if isinstance(payload.get("results"), list):
        return payload["results"]
    data = payload.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("documents"), list):
            return data["documents"]
        if isinstance(data.get("content"), list):
            return data["content"]
    return []


def _normalize_doc(doc, include_raw=False):
    ius = doc.get("ius") or doc.get("registroDigital") or doc.get("id")
    semanal_raw = doc.get("semanal")
    if semanal_raw is None:
        semanal_raw = doc.get("isSemanal")

    if semanal_raw is None:
        is_semanal = None
    else:
        is_semanal = semanal_raw is True or semanal_raw == 1 or str(semanal_raw) == "1"

    rubro = str(doc.get("rubro") or doc.get("rubroTexto") or "").strip().upper()
    texto = str(doc.get("textoPublicacion") or doc.get("texto") or "")

    item = {
        "ius": ius,
        "isSemanal": is_semanal,
        "rubro": rubro,
        "fechaPublicacion": doc.get("fechaPublicacion") or doc.get("fecha") or "",
        "instancia": doc.get("instancia") or "",
        "epoca": doc.get("epoca") or "",
        "tipoDocumento": doc.get("tipoDocumento") or "",
        "textoSnippet": texto[:500],
    }
    if include_raw:
        item["raw"] = doc
    return item


def _to_int(value, fallback):
    try:
        return int(value)
    except Exception:
        return fallback


@app.get("/sjf/search")
@app.get("/jurisprudencia/buscar")
def sjf_search(
    q: str = Query(default=""),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=10, ge=1, le=50),
    includeRaw: bool = Query(default=False),
):
    payload = _default_sjf_payload(q)
    url = f"{SJF_BASE}/tesis?page={page}&size={size}"
    status, data = _http_json(url, method="POST", body=payload, headers=_sjf_headers(content_type=True))
    if status >= 400:
        return JSONResponse(
            status_code=status,
            content={"error": "SJF upstream error", "status": status, "upstream": data},
        )

    docs = _extract_docs(data)
    if isinstance(data, dict):
        raw_total = data.get("total") or data.get("totalElements")
        raw_pages = data.get("totalPages") or data.get("pages")
    else:
        raw_total = None
        raw_pages = None
    total = _to_int(raw_total, len(docs))
    total_pages = _to_int(raw_pages, int((total + size - 1) / size) if size else 0)
    items = [_normalize_doc(doc, include_raw=includeRaw) for doc in docs]

    return {
        "total": total,
        "totalPages": total_pages,
        "page": page,
        "size": size,
        "count": len(items),
        "hasMore": (page + 1) < total_pages,
        "items": items,
    }


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

    url = f"{SJF_BASE}/tesis?page={page}&size={size}"
    status, data = _http_json(url, method="POST", body=payload, headers=_sjf_headers(content_type=True))
    if status >= 400:
        return JSONResponse(
            status_code=status,
            content={"error": "SJF upstream error", "status": status, "upstream": data},
        )

    docs = _extract_docs(data)
    if isinstance(data, dict):
        raw_total = data.get("total") or data.get("totalElements")
        raw_pages = data.get("totalPages") or data.get("pages")
    else:
        raw_total = None
        raw_pages = None
    total = _to_int(raw_total, len(docs))
    total_pages = _to_int(raw_pages, int((total + size - 1) / size) if size else 0)
    items = [_normalize_doc(doc, include_raw=includeRaw) for doc in docs]

    return {
        "total": total,
        "totalPages": total_pages,
        "page": page,
        "size": size,
        "count": len(items),
        "hasMore": (page + 1) < total_pages,
        "items": items,
    }


@app.get("/sjf/detail")
@app.get("/jurisprudencia/detalle")
def sjf_detail(
    ius: int,
    isSemanal: Optional[bool] = Query(default=None),
    hostName: Optional[str] = Query(default="https://sjf2.scjn.gob.mx"),
    includeRaw: Optional[bool] = Query(default=False),
):
    hostName = hostName or "https://sjf2.scjn.gob.mx"
    includeRaw = bool(includeRaw)
    def call_detail(sem):
        params = {"hostName": hostName}
        if sem is True:
            params["isSemanal"] = "true"
        query = parse.urlencode(params)
        url = f"{SJF_BASE}/tesis/{ius}?{query}"
        return _http_json(url, method="GET", headers=_sjf_headers(content_type=False)), sem

    if isSemanal is None:
        (status, data), used = call_detail(True)
        if status >= 400:
            (status, data), used = call_detail(False)
    else:
        (status, data), used = call_detail(bool(isSemanal))

    if status >= 400:
        return JSONResponse(
            status_code=status,
            content={"error": "SJF detail request failed", "status": status, "upstream": data},
        )

    texto = ""
    if isinstance(data, dict):
        texto = str(data.get("texto") or data.get("textoPublicacion") or data.get("contenido") or "")
    elif isinstance(data, str):
        texto = data

    response = {
        "ius": ius,
        "isSemanalUsed": used is True,
        "hostName": hostName,
        "rubro": str(data.get("rubro") if isinstance(data, dict) else "").upper(),
        "fechaPublicacion": str(data.get("fechaPublicacion") if isinstance(data, dict) else ""),
        "titulo": str((data.get("titulo") or data.get("title")) if isinstance(data, dict) else ""),
        "texto": texto,
        "textoPlano": texto.replace("\r\n", "\n").strip(),
    }
    if includeRaw:
        response["raw"] = data
    return response

@app.get("/")
def read_root():
    return {
        "mensaje": "API de LexIA funcionando correctamente",
        "servicio": "LexIA-api",
        "status": "ok",
    }


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "LexIA-api"}

@app.get("/ley")
def buscar_ley(id: Optional[int] = None, categoria: Optional[int] = None, nombre: Optional[str] = None):
    resultados = leyes
    if id is not None:
        resultados = [l for l in resultados if l["id"] == id]
    if categoria is not None:
        resultados = [l for l in resultados if l["categoria"] == categoria]
    if nombre is not None:
        resultados = [l for l in resultados if nombre.lower() in l["nombre"].lower()]
    return JSONResponse(content=resultados)
