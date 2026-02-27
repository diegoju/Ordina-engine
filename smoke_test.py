#!/usr/bin/env python3
import json
import os
import sys
from urllib import error, parse, request


BASE_URL = os.getenv("LEXIA_BASE_URL", "https://lexia-api.vercel.app").rstrip("/")
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


def main():
    print(f"Running smoke tests against: {BASE_URL}")
    checks = [
        ("health", check_health),
        ("ley constitucion", check_ley_constitucion),
        ("sjf search", check_sjf_search),
        ("jurislex search", check_jurislex_search),
        ("jurislex detail", check_jurislex_detail),
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
