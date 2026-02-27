from fastapi import Body, FastAPI, Query
import json
import html
import re
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
JURISLEX_BASE = "https://jurislex.scjn.gob.mx/Legislaciones.Datos64/Aplicacion/Legislaciones.svc/web"


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


def _jurislex_headers(content_type=False):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:148.0) Gecko/20100101 Firefox/148.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://jurislex.scjn.gob.mx",
        "Referer": "https://jurislex.scjn.gob.mx/",
    }
    if content_type:
        headers["Content-Type"] = "application/json;charset=utf-8"
    cookie = os.getenv("JURISLEX_COOKIE")
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


def _to_int(value, fallback):
    try:
        return int(value)
    except Exception:
        return fallback


def _to_bool(value, fallback=False):
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes", "si")


def _strip_html(value):
    text = str(value or "")
    text = html.unescape(text)
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = text.replace("</p>", "\n\n").replace("<p>", "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _jurislex_filter_raw(id_legislacion: int, articulo_numero: Optional[int] = None):
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


def _normalize_jurislex_result(item, include_raw=False):
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
    return response


@app.get("/jurislex/decretos")
def jurislex_decretos(
    idLegislacion: int = Query(...),
    idOrdenamiento: Optional[int] = Query(default=None),
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
    categoria: int = Query(..., description="Categoria Jurislex"),
    idLegislacion: int = Query(..., description="IdLegislacion validado"),
    desc: str = Query(default="", description="Numero de articulo o palabra"),
    soloArticulo: bool = Query(default=False),
    indice: int = Query(default=0, ge=0),
    elementos: int = Query(default=20, ge=1, le=50),
    articuloNumero: Optional[int] = Query(default=None, description="Numero base para filtro exacto"),
    includeRaw: bool = Query(default=False),
):
    payload = {
        "datosArticulo": {
            "Indice": indice,
            "Elementos": elementos,
            "Ordenamiento": "A desc",
            "IdLegislacion": [int(idLegislacion)],
            "SoloArticulo": _to_bool(soloArticulo, False),
            "Desc": str(desc or ""),
            "SoloIndices": False,
            "filterRaw": _jurislex_filter_raw(int(idLegislacion), articuloNumero),
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
    categoria: int = Query(...),
    idLegislacion: int = Query(...),
    idArticulo: int = Query(...),
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
        "titulo": detail.get("sTitulo") or "",
        "capitulo": detail.get("sCapitulo") or "",
        "texto": detail.get("sDescArticulo") or "",
        "textoPlano": _strip_html(detail.get("sDescArticulo") or ""),
    }
    if includeRaw:
        response["raw"] = detail
    return response

@app.get("/")
def read_root():
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
    resultados = leyes
    if id is not None:
        resultados = [l for l in resultados if l["id"] == id]
    if categoria is not None:
        resultados = [l for l in resultados if l["categoria"] == categoria]
    if nombre is not None:
        resultados = [l for l in resultados if nombre.lower() in l["nombre"].lower()]
    return JSONResponse(content=resultados)
