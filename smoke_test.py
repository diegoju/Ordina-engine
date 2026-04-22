#!/usr/bin/env python3
import json
import os
import sys
from urllib import error, parse, request


BASE_URL = os.getenv("ORDINA_BASE_URL", os.getenv("LEXIA_BASE_URL", "https://ordina-engine.vercel.app")).rstrip("/")
TIMEOUT = 25


def get_json(path, query=None):
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"
    req = request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    with request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return resp.status, json.loads(raw)


def post_json(path, payload):
    url = f"{BASE_URL}{path}"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, method="POST")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    with request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return resp.status, json.loads(raw)


def run_check(name, fn):
    try:
        fn()
        print(f"[OK] {name}")
        return True
    except Exception as exc:
        print(f"[FAIL] {name}: {exc}")
        return False


def check_health():
    status, data = get_json("/health")
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if data.get("status") != "ok":
        raise ValueError(f"status inesperado: {data.get('status')}")


def check_ley_constitucion():
    status, data = get_json("/ley", {"nombre": "constitución"})
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if not isinstance(data, list) or len(data) == 0:
        raise ValueError("sin resultados")
    first = data[0]
    if "id" not in first or "categoria" not in first:
        raise ValueError("respuesta sin id/categoria")


def check_normas_unificadas():
    status, data = get_json("/normas/buscar", {"nombre": "ley de amparo", "size": 5})
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if data.get("count", 0) < 1:
        raise ValueError("sin resultados unificados")
    items = data.get("items") or []
    if not items or not items[0].get("rutaSugerida"):
        raise ValueError("sin ruta sugerida")
    if not any("jurislex" in (item.get("disponibleEn") or []) for item in items):
        raise ValueError("sin disponibilidad en jurislex")


def check_normas_articulos_unificados():
    status, data = get_json(
        "/normas/articulos/buscar",
        {
            "nombre": "Ley de los Derechos de las Personas Adultas Mayores",
            "articulo": 50,
        },
    )
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if data.get("fuenteUsada") != "jurislex":
        raise ValueError("se esperaba fuente jurislex")
    if data.get("count", 0) < 1:
        raise ValueError("sin articulos unificados en jurislex")

    status, data = get_json(
        "/normas/articulos/buscar",
        {
            "nombre": "Ley General del Sistema de Medios de Impugnacion en Materia Electoral",
            "articulo": 40,
            "categoriaOrdenamiento": "LEY",
            "ambito": "FEDERAL",
        },
    )
    if status != 200:
        raise ValueError(f"HTTP {status} en SIL")
    if data.get("fuenteUsada") != "sil":
        raise ValueError("se esperaba fuente sil")
    if data.get("count", 0) < 1:
        raise ValueError("sin articulos unificados en sil")


def check_normas_articulos_detalle_unificado():
    status, data = get_json(
        "/normas/articulos/detalle",
        {
            "nombre": "Ley de los Derechos de las Personas Adultas Mayores",
            "articulo": 50,
        },
    )
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if data.get("fuenteUsada") != "jurislex":
        raise ValueError("detalle esperaba fuente jurislex")
    articulo = data.get("articulo") or {}
    if "TÍTULO" not in str(articulo.get("titulo") or "").upper():
        raise ValueError("detalle jurislex sin titulo")
    if "CAPÍTULO" not in str(articulo.get("capitulo") or "").upper():
        raise ValueError("detalle jurislex sin capitulo")

    status, data = get_json(
        "/normas/articulos/detalle",
        {
            "nombre": "Ley General del Sistema de Medios de Impugnacion en Materia Electoral",
            "articulo": 40,
            "categoriaOrdenamiento": "LEY",
            "ambito": "FEDERAL",
        },
    )
    if status != 200:
        raise ValueError(f"HTTP {status} en detalle SIL")
    if data.get("fuenteUsada") != "sil":
        raise ValueError("detalle esperaba fuente sil")
    articulo = data.get("articulo") or {}
    if "TITULO" not in str(articulo.get("titulo") or "").upper():
        raise ValueError("detalle sil sin titulo")
    if "CAPITULO" not in str(articulo.get("capitulo") or "").upper():
        raise ValueError("detalle sil sin capitulo")


def check_sjf_search():
    status, data = get_json(
        "/jurisprudencia/buscar",
        {
            "q": "amparo",
            "page": 0,
            "size": 1,
        },
    )
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if data.get("count", 0) < 1:
        raise ValueError("count < 1")
    items = data.get("items") or []
    if not items or not items[0].get("ius"):
        raise ValueError("sin ius en resultado")


def check_jurislex_search():
    status, data = get_json(
        "/jurislex/articulos/buscar",
        {
            "categoria": 1000,
            "idLegislacion": 1000,
            "desc": 1,
            "soloArticulo": "true",
            "elementos": 1,
        },
    )
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if data.get("count", 0) < 1:
        raise ValueError("count < 1")
    items = data.get("items") or []
    if not items or not items[0].get("idArticulo"):
        raise ValueError("sin idArticulo en resultado")


def check_jurislex_detail():
    status, data = get_json(
        "/jurislex/articulos/detalle",
        {
            "categoria": 1000,
            "idLegislacion": 1000,
            "idArticulo": 10,
        },
    )
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if not str(data.get("texto") or "").strip():
        raise ValueError("texto vacío")


def check_precedentes_search():
    status, data = get_json(
        "/precedentes/buscar",
        {
            "q": "partido*",
            "indice": "ejecutorias",
            "page": 1,
            "size": 1,
        },
    )
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if data.get("count", 0) < 1:
        raise ValueError("count < 1")
    items = data.get("items") or []
    if not items or not items[0].get("registroDigital"):
        raise ValueError("sin registroDigital en resultado")


def check_legislacion_search():
    status, data = get_json(
        "/legislacion/buscar",
        {
            "q": "Electoral",
            "fuente": "SIL",
            "indice": "legislacion",
            "categoriaOrdenamiento": "LEY",
            "ambito": "FEDERAL",
            "page": 1,
            "size": 1,
        },
    )
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if data.get("count", 0) < 1:
        raise ValueError("count < 1")
    items = data.get("items") or []
    if not items or not str(items[0].get("ordenamiento") or "").strip():
        raise ValueError("sin ordenamiento en resultado")


def check_legislacion_detail():
    status, data = get_json("/legislacion/detalle", {"id": 9006})
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if not str(data.get("ordenamiento") or "").strip():
        raise ValueError("sin ordenamiento en detalle")
    if data.get("totalBloques", 0) < 1:
        raise ValueError("sin bloques en detalle")
    bloques = data.get("bloques") or []
    if not bloques or not str(bloques[0].get("contenidoPlano") or "").strip():
        raise ValueError("sin contenido en bloques")


def check_legislacion_articulos_search():
    status, data = get_json("/legislacion/articulos/buscar", {"id": 9006, "articulo": 1})
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if data.get("count", 0) < 1:
        raise ValueError("sin articulos encontrados")
    items = data.get("items") or []
    if not items or items[0].get("numero") != 1:
        raise ValueError("sin articulo 1 en resultado")
    status, data = get_json("/legislacion/articulos/buscar", {"id": 9006, "q": "orden publico"})
    if status != 200:
        raise ValueError(f"HTTP {status} en busqueda por texto")
    if data.get("count", 0) < 1:
        raise ValueError("sin coincidencias por texto libre")


def check_citas_extraction():
    status, data = post_json(
        "/citas/extraer",
        {
            "fuente": "smoke.txt",
            "texto": "Con fundamento en el artículo 14 de la Ley de Amparo, el artículo 16 constitucional y el registro digital 2023456.",
            "resolver": True,
        },
    )
    if status != 200:
        raise ValueError(f"HTTP {status}")
    if data.get("resumen", {}).get("totalCitas", 0) < 2:
        raise ValueError("se esperaban al menos 2 citas")
    items = data.get("items") or []
    if not any(item.get("tipo") == "articulo" for item in items):
        raise ValueError("sin articulo detectado")
    if not any(item.get("registroDigital") == "2023456" for item in items):
        raise ValueError("sin registro digital detectado")
    if not any(item.get("tipo") == "articulo" and item.get("textoCita") for item in items):
        raise ValueError("sin textoCita en articulos resueltos")


def main():
    print(f"Running smoke tests against: {BASE_URL}")
    checks = [
        ("health", check_health),
        ("ley constitucion", check_ley_constitucion),
        ("normas unificadas", check_normas_unificadas),
        ("normas articulos unificados", check_normas_articulos_unificados),
        ("normas articulos detalle", check_normas_articulos_detalle_unificado),
        ("sjf search", check_sjf_search),
        ("jurislex search", check_jurislex_search),
        ("jurislex detail", check_jurislex_detail),
        ("precedentes search", check_precedentes_search),
        ("legislacion search", check_legislacion_search),
        ("legislacion detail", check_legislacion_detail),
        ("legislacion articulos", check_legislacion_articulos_search),
        ("citas extraction", check_citas_extraction),
    ]

    ok = True
    for name, fn in checks:
        ok = run_check(name, fn) and ok

    if not ok:
        sys.exit(1)
    print("All smoke tests passed.")


if __name__ == "__main__":
    try:
        main()
    except error.HTTPError as exc:
        print(f"[FAIL] HTTP error global: {exc.code}")
        sys.exit(1)
    except Exception as exc:
        print(f"[FAIL] Error global: {exc}")
        sys.exit(1)
