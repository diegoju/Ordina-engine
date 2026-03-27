from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "mcp_server.py"


def _send_message(process: subprocess.Popen[bytes], payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")

    assert process.stdin is not None
    process.stdin.write(header + body)
    process.stdin.flush()

    assert process.stdout is not None
    headers: dict[str, str] = {}
    while True:
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("El servidor MCP no devolvio respuesta")
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("ascii", errors="replace").strip()
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    length = int(headers["content-length"])
    raw = process.stdout.read(length)
    return json.loads(raw.decode("utf-8"))


def main() -> None:
    process = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        init_response = _send_message(
            process,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        print("initialize:")
        print(json.dumps(init_response, ensure_ascii=False, indent=2))

        consulta_response = _send_message(
            process,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "consultaJuridicaCompleta",
                    "arguments": {
                        "consulta": "articulo 1 de la constitucion",
                        "nombreLey": "Constitucion Politica de los Estados Unidos Mexicanos",
                        "numeroArticulo": 1,
                    },
                },
            },
        )
        print("\nconsultaJuridicaCompleta:")
        print(json.dumps(consulta_response, ensure_ascii=False, indent=2))
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    main()
