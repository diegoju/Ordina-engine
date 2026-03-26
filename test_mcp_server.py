from __future__ import annotations

import sys
import unittest
import json
import subprocess
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_server


class McpServerTests(unittest.TestCase):
    def test_initialize_exposes_tools_resources_and_prompts(self) -> None:
        result = mcp_server._dispatch("initialize", {})
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertIn("tools", result["capabilities"])
        self.assertIn("resources", result["capabilities"])
        self.assertIn("prompts", result["capabilities"])

    def test_tools_list_contains_new_composite_tool(self) -> None:
        result = mcp_server._dispatch("tools/list", {})
        names = {tool["name"] for tool in result["tools"]}
        self.assertIn("consultaJuridicaCompleta", names)
        self.assertIn("buscarArticuloPorLeyYNumero", names)
        self.assertIn("buscarPrecedentes", names)

    def test_resources_list_contains_readme(self) -> None:
        result = mcp_server._dispatch("resources/list", {})
        uris = {resource["uri"] for resource in result["resources"]}
        self.assertIn("ordina://readme", uris)
        self.assertIn("ordina://openapi/hub", uris)

    def test_prompts_get_returns_message(self) -> None:
        result = mcp_server._dispatch(
            "prompts/get",
            {"name": "buscar-articulo", "arguments": {"nombreLey": "Constitucion", "numeroArticulo": 1}},
        )
        self.assertEqual(result["messages"][0]["role"], "user")
        self.assertIn("articulo 1", result["messages"][0]["content"]["text"])

    def test_tools_call_marks_error_when_wrapper_returns_error(self) -> None:
        with patch.object(
            mcp_server,
            "buscar_precedentes",
            return_value={
                "ok": False,
                "error": {"code": "UPSTREAM_TIMEOUT", "message": "timeout", "status": 504},
            },
        ):
            original = mcp_server.TOOLS["buscarPrecedentes"]["handler"]
            mcp_server.TOOLS["buscarPrecedentes"]["handler"] = mcp_server.buscar_precedentes
            try:
                result = mcp_server._dispatch(
                    "tools/call",
                    {"name": "buscarPrecedentes", "arguments": {"q": "amparo"}},
                )
            finally:
                mcp_server.TOOLS["buscarPrecedentes"]["handler"] = original
        self.assertTrue(result["isError"])
        self.assertIn("UPSTREAM_TIMEOUT", result["content"][0]["text"])

    def test_consulta_juridica_completa_routes_to_articulo(self) -> None:
        fake_result = {
            "count": 1,
            "items": [{"idArticulo": 10}],
            "selectedLaw": {"id": 1, "nombre": "Ley X"},
            "detail": {"idArticulo": 10, "textoPlano": "detalle"},
        }
        with patch.object(mcp_server, "obtener_articulo_por_ley_y_numero", return_value=fake_result) as mocked:
            result = mcp_server.consulta_juridica_completa(
                consulta="articulo 1 constitucional",
                nombreLey="Constitucion",
                numeroArticulo=1,
            )
        mocked.assert_called_once()
        self.assertEqual(result["strategyUsed"], "articulo")
        self.assertEqual(result["result"], fake_result)

    def test_obtener_articulo_por_ley_y_numero_adds_detail(self) -> None:
        search_result = {
            "count": 1,
            "items": [{"idArticulo": 77, "numeroArticulo": "1"}],
            "selectedLaw": {"id": 1000, "categoria": 2, "nombre": "Constitucion"},
        }
        detail_result = {"idArticulo": 77, "textoPlano": "detalle del articulo"}
        with patch.object(mcp_server, "buscar_articulo_por_ley_y_numero", return_value=search_result) as mocked_search:
            with patch.object(mcp_server, "obtener_detalle_articulo_jurislex", return_value=detail_result) as mocked_detail:
                result = mcp_server.obtener_articulo_por_ley_y_numero("Constitucion", 1)
        mocked_search.assert_called_once()
        mocked_detail.assert_called_once()
        self.assertEqual(result["selectedItem"]["idArticulo"], 77)
        self.assertEqual(result["detail"]["textoPlano"], "detalle del articulo")

    def test_consulta_juridica_completa_routes_to_jurisprudencia(self) -> None:
        fake_result = {"selectedItem": {"ius": 12345}, "detail": {"ius": 12345}}
        with patch.object(mcp_server, "buscar_y_detallar_jurisprudencia", return_value=fake_result) as mocked:
            result = mcp_server.consulta_juridica_completa(consulta="jurisprudencia sobre amparo")
        mocked.assert_called_once()
        self.assertEqual(result["strategyUsed"], "jurisprudencia")
        self.assertEqual(result["result"]["detail"]["ius"], 12345)

    def test_consulta_juridica_completa_returns_warning_for_missing_article_number(self) -> None:
        result = mcp_server.consulta_juridica_completa(consulta="articulo constitucional", estrategia="articulo")
        self.assertEqual(result["strategyUsed"], "articulo")
        self.assertIn("numeroArticulo", result["warnings"][0])


class McpServerStdioTests(unittest.TestCase):
    def _send_message(self, process: subprocess.Popen[bytes], payload: dict) -> dict:
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
                raise RuntimeError("No se recibio respuesta del servidor MCP")
            if line in (b"\r\n", b"\n"):
                break
            text = line.decode("ascii", errors="replace").strip()
            if ":" in text:
                key, value = text.split(":", 1)
                headers[key.strip().lower()] = value.strip()

        length = int(headers["content-length"])
        raw = process.stdout.read(length)
        return json.loads(raw.decode("utf-8"))

    def test_stdio_initialize_and_tools_list(self) -> None:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "mcp_server.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            init_response = self._send_message(
                process,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            self.assertEqual(init_response["result"]["serverInfo"]["name"], "Ordina-engine")

            tools_response = self._send_message(
                process,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            names = {tool["name"] for tool in tools_response["result"]["tools"]}
            self.assertIn("consultaJuridicaCompleta", names)
            self.assertIn("obtenerArticuloPorLeyYNumero", names)
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
    unittest.main()
