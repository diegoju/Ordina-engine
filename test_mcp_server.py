from __future__ import annotations

import sys
import time
import unittest
import json
import subprocess
from pathlib import Path
from fastapi import HTTPException
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_server
import mcp_http_server
import api


class McpServerTests(unittest.TestCase):
    def test_buscar_ley_matches_oaxaca_with_partial_tokens(self) -> None:
        result = api.buscar_ley(nombre="penal Oaxaca")
        payload = json.loads(bytes(result.body).decode("utf-8"))
        names = {item["nombre"] for item in payload}
        self.assertIn("Código Penal para el Estado Libre y Soberano de Oaxaca", names)

    def test_buscar_ley_matches_oaxaca_without_accents(self) -> None:
        result = api.buscar_ley(nombre="Codigo Penal para el Estado de Oaxaca")
        payload = json.loads(bytes(result.body).decode("utf-8"))
        names = {item["nombre"] for item in payload}
        self.assertIn("Código Penal para el Estado Libre y Soberano de Oaxaca", names)

    def test_resolver_ley_por_nombre_prioritizes_constitucion_real(self) -> None:
        result = mcp_server.resolver_ley_por_nombre("constitucion")
        selected = result["selectedLaw"] or {}
        self.assertEqual(selected.get("id"), 1000)

    def test_consulta_juridica_completa_cleans_article_query_when_law_hint_missing(self) -> None:
        fake_result = {"selectedLaw": {"id": 6048, "nombre": "Código Penal para el Estado Libre y Soberano de Oaxaca"}}
        with patch.object(mcp_server, "obtener_articulo_por_ley_y_numero", return_value=fake_result) as mocked:
            result = mcp_server.consulta_juridica_completa(consulta="codigo penal oaxaca articulo 1")
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs["nombreLey"], "codigo penal oaxaca")
        self.assertEqual(result["strategyUsed"], "articulo")

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
        self.assertIn("buscarLegislacionSCJN", names)
        self.assertIn("obtenerDetalleLegislacionSCJN", names)
        self.assertIn("buscarArticulosLegislacionSCJN", names)

    def test_resources_list_contains_readme(self) -> None:
        result = mcp_server._dispatch("resources/list", {})
        uris = {resource["uri"] for resource in result["resources"]}
        self.assertIn("ordina://readme", uris)
        self.assertIn("ordina://openapi/hub", uris)
        self.assertIn("ordina://guia-flujos", uris)

    def test_prompts_get_returns_message(self) -> None:
        result = mcp_server._dispatch(
            "prompts/get",
            {"name": "buscar-articulo", "arguments": {"nombreLey": "Constitucion", "numeroArticulo": 1}},
        )
        self.assertEqual(result["messages"][0]["role"], "user")
        self.assertIn("articulo 1", result["messages"][0]["content"]["text"])

    def test_prompts_list_contains_flow_specific_prompts(self) -> None:
        result = mcp_server._dispatch("prompts/list", {})
        names = {prompt["name"] for prompt in result["prompts"]}
        self.assertIn("usar-jurislex-correctamente", names)
        self.assertIn("texto-completo-jurisprudencia", names)
        self.assertIn("resolver-ley-estatal", names)

    def test_tool_success_includes_full_text_for_jurisprudencia_detail(self) -> None:
        result = mcp_server._tool_success(
            {
                "ius": 2030687,
                "titulo": "DERECHO HUMANO AL AGUA",
                "fechaPublicacion": "2024-01-01",
                "textoPlano": "Texto completo de la tesis.",
            }
        )
        self.assertIn("DERECHO HUMANO AL AGUA", result["content"][0]["text"])
        self.assertIn("Texto completo de la tesis.", result["content"][0]["text"])

    def test_tool_success_includes_article_detail_text_for_composite_result(self) -> None:
        result = mcp_server._tool_success(
            {
                "selectedLaw": {"nombre": "Código Penal para el Estado Libre y Soberano de Oaxaca"},
                "selectedItem": {"numeroArticulo": 8},
                "detail": {
                    "titulo": "CAPÍTULO ÚNICO.",
                    "textoPlano": "Artículo 8. Cuando se realice una conducta prevista como delito...",
                },
            }
        )
        self.assertIn("Código Penal para el Estado Libre y Soberano de Oaxaca", result["content"][0]["text"])
        self.assertIn("Artículo 8", result["content"][0]["text"])
        self.assertIn("Cuando se realice una conducta prevista como delito", result["content"][0]["text"])

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
        self.assertEqual(result["resolvedNumeroArticulo"], 1)
        self.assertEqual(result["confidence"], "alta")
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

    def test_buscar_articulo_por_ley_y_numero_uses_broad_fallback_when_exact_filter_fails(self) -> None:
        exact_empty = {"count": 0, "items": []}
        broad_result = {
            "count": 2,
            "items": [
                {"idArticulo": 10, "numeroArticulo": 1},
                {"idArticulo": 20, "numeroArticulo": 2},
            ],
        }
        with patch.object(mcp_server, "_law_matches", return_value=[{"id": 1000, "categoria": 1000, "nombre": "Constitución"}]):
            with patch.object(mcp_server, "buscar_articulos_jurislex", side_effect=[exact_empty, broad_result]) as mocked_search:
                result = mcp_server.buscar_articulo_por_ley_y_numero("constitucion", 1)
        self.assertEqual(mocked_search.call_count, 2)
        self.assertEqual(result["selectedLaw"]["id"], 1000)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["idArticulo"], 10)

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

    def test_consulta_juridica_completa_infers_article_metadata(self) -> None:
        fake_result = {"selectedLaw": {"nombre": "Constitucion"}, "detail": {"textoPlano": "detalle"}}
        with patch.object(mcp_server, "obtener_articulo_por_ley_y_numero", return_value=fake_result):
            result = mcp_server.consulta_juridica_completa(
                consulta="Quiero el articulo 5 de la Ley Federal del Trabajo"
            )
        self.assertEqual(result["strategyUsed"], "articulo")
        self.assertEqual(result["resolvedNumeroArticulo"], 5)
        self.assertEqual(result["resolvedNombreLey"], "ley federal del trabajo")

    def test_consulta_juridica_completa_infers_article_metadata_with_accents(self) -> None:
        fake_result = {"selectedLaw": {"nombre": "Código Penal para el Estado Libre y Soberano de Oaxaca"}}
        with patch.object(mcp_server, "obtener_articulo_por_ley_y_numero", return_value=fake_result) as mocked:
            result = mcp_server.consulta_juridica_completa(
                consulta="dame el primer párrafo del artículo 8 del código penal de oaxaca"
            )
        self.assertEqual(result["strategyUsed"], "articulo")
        self.assertEqual(result["resolvedNumeroArticulo"], 8)
        self.assertEqual(mocked.call_args.kwargs["nombreLey"], "codigo penal de oaxaca")

    def test_consulta_juridica_completa_routes_to_precedente(self) -> None:
        fake_result = {"count": 2, "items": [{"registroDigital": 1}, {"registroDigital": 2}]}
        with patch.object(mcp_server, "buscar_precedentes", return_value=fake_result) as mocked:
            result = mcp_server.consulta_juridica_completa(consulta="precedentes sobre libertad de expresion")
        mocked.assert_called_once()
        self.assertEqual(result["strategyUsed"], "precedente")
        self.assertIn("Precedentes localizados", result["summary"])

    def test_obtener_articulo_por_ley_y_numero_propagates_detail_error(self) -> None:
        search_result = {
            "count": 1,
            "items": [{"idArticulo": 77, "numeroArticulo": "1"}],
            "selectedLaw": {"id": 1000, "categoria": 2, "nombre": "Constitucion"},
        }
        detail_error = {"ok": False, "error": {"code": "UPSTREAM_UNAVAILABLE", "message": "boom", "status": 502}}
        with patch.object(mcp_server, "buscar_articulo_por_ley_y_numero", return_value=search_result):
            with patch.object(mcp_server, "obtener_detalle_articulo_jurislex", return_value=detail_error):
                result = mcp_server.obtener_articulo_por_ley_y_numero("Constitucion", 1)
        self.assertTrue(result["detail"]["ok"] is False)

    def test_busqueda_jurisprudencia_propagates_upstream_error(self) -> None:
        error_payload = {"ok": False, "error": {"code": "UPSTREAM_TIMEOUT", "message": "timeout", "status": 504}}
        with patch.object(mcp_server, "buscar_jurisprudencia", return_value=error_payload):
            result = mcp_server.buscar_y_detallar_jurisprudencia("amparo")
        self.assertEqual(result["error"]["code"], "UPSTREAM_TIMEOUT")


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

    def test_stdio_resources_and_prompts(self) -> None:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "mcp_server.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            _ = self._send_message(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            resources = self._send_message(
                process,
                {"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}},
            )
            prompts = self._send_message(
                process,
                {"jsonrpc": "2.0", "id": 3, "method": "prompts/list", "params": {}},
            )
            self.assertIn("ordina://readme", {item["uri"] for item in resources["result"]["resources"]})
            self.assertIn("consulta-juridica-segura", {item["name"] for item in prompts["result"]["prompts"]})
        finally:
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.terminate()
            process.wait(timeout=5)


class McpHttpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        mcp_http_server.ACTIVE_SESSIONS.clear()

    def test_initialize_creates_session_header(self) -> None:
        messages, _ = mcp_http_server._normalize_messages(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        headers = mcp_http_server._session_headers(messages)
        self.assertIn(mcp_http_server.SESSION_HEADER, headers)
        self.assertIn(headers[mcp_http_server.SESSION_HEADER], mcp_http_server.ACTIVE_SESSIONS)

    def test_non_initialize_requires_existing_session(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            mcp_http_server._ensure_session("invalida", initialize_only=False)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_collect_responses_returns_jsonrpc_result(self) -> None:
        responses = mcp_http_server._collect_responses(
            [{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}]
        )
        self.assertEqual(responses[0]["jsonrpc"], "2.0")
        self.assertIn("tools", responses[0]["result"])


class ApiHelperTests(unittest.TestCase):
    """Tests for new helpers added during hardening."""

    # --- _extract_results ---

    def test_extract_results_finds_documents_key(self) -> None:
        payload = {"documents": [{"ius": 1}, {"ius": 2}]}
        self.assertEqual(api._extract_results(payload, "documents", "content"), [{"ius": 1}, {"ius": 2}])

    def test_extract_results_finds_nested_under_data(self) -> None:
        payload = {"data": {"content": [{"ius": 3}]}}
        self.assertEqual(api._extract_results(payload, "documents", "content"), [{"ius": 3}])

    def test_extract_results_returns_empty_for_non_dict(self) -> None:
        self.assertEqual(api._extract_results(None, "documents"), [])  # type: ignore[arg-type]
        self.assertEqual(api._extract_results([], "documents"), [])    # type: ignore[arg-type]

    def test_extract_results_returns_empty_when_no_key_matches(self) -> None:
        self.assertEqual(api._extract_results({"other": [1, 2]}, "documents", "content"), [])

    def test_extract_docs_wrapper_uses_sjf_keys(self) -> None:
        payload = {"content": [{"ius": 5}]}
        self.assertEqual(api._extract_docs(payload), [{"ius": 5}])

    def test_extract_bj_items_wrapper_uses_resultados_key(self) -> None:
        payload = {"resultados": [{"registroDigital": 99}]}
        self.assertEqual(api._extract_bj_items(payload), [{"registroDigital": 99}])

    # --- TTL cache ---

    def setUp(self) -> None:
        api._cache.clear()

    def test_set_cached_stores_entry(self) -> None:
        key = api._cache_key("http://example.com", "GET", None)
        api._set_cached(key, 200, {"ok": True})
        self.assertIn(key, api._cache)

    def test_get_cached_returns_stored_value(self) -> None:
        key = api._cache_key("http://example.com/search", "POST", {"q": "amparo"})
        api._set_cached(key, 200, {"items": []})
        result = api._get_cached(key)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[1], {"items": []})

    def test_set_cached_does_not_store_errors(self) -> None:
        key = api._cache_key("http://example.com/fail", "GET", None)
        api._set_cached(key, 502, {"error": "boom"})
        self.assertNotIn(key, api._cache)

    def test_get_cached_returns_none_after_ttl(self) -> None:
        key = api._cache_key("http://example.com/ttl", "GET", None)
        # Manually insert an expired entry
        api._cache[key] = (time.time() - api._CACHE_TTL - 1, 200, {"stale": True})
        self.assertIsNone(api._get_cached(key))
        self.assertNotIn(key, api._cache)

    def test_cache_key_differs_by_body(self) -> None:
        k1 = api._cache_key("http://x.com", "POST", {"q": "amparo"})
        k2 = api._cache_key("http://x.com", "POST", {"q": "tesis"})
        self.assertNotEqual(k1, k2)

    def test_cache_key_same_for_identical_inputs(self) -> None:
        k1 = api._cache_key("http://x.com", "POST", {"b": 2, "a": 1})
        k2 = api._cache_key("http://x.com", "POST", {"a": 1, "b": 2})
        self.assertEqual(k1, k2)  # sort_keys ensures stability


class McpSuffixTests(unittest.TestCase):
    """Tests for article suffix extraction and matching."""

    def test_extract_article_suffix_returns_b_from_167_b(self) -> None:
        self.assertEqual(mcp_server._extract_article_suffix("artículo 167-B"), "B")

    def test_extract_article_suffix_returns_none_when_no_suffix(self) -> None:
        self.assertIsNone(mcp_server._extract_article_suffix("artículo 167"))

    def test_extract_article_suffix_returns_none_for_empty(self) -> None:
        self.assertIsNone(mcp_server._extract_article_suffix(""))

    def test_extract_article_number_still_returns_int_with_suffix(self) -> None:
        self.assertEqual(mcp_server._extract_article_number("artículo 167-B"), 167)

    def test_article_matches_number_matches_without_suffix(self) -> None:
        item = {"numeroArticulo": 167}
        self.assertTrue(mcp_server._article_matches_number(item, 167))

    def test_article_matches_number_rejects_wrong_number(self) -> None:
        item = {"numeroArticulo": 168}
        self.assertFalse(mcp_server._article_matches_number(item, 167))

    def test_article_matches_number_with_suffix_rejects_when_text_has_different_suffix(self) -> None:
        # Item text mentions 167 (no B), but we're looking for 167-B
        item = {"numeroArticulo": 167, "texto": "Artículo 167. Las obligaciones del patrón..."}
        # Should reject because text doesn't contain "167-B"
        self.assertFalse(mcp_server._article_matches_number(item, 167, suffix="B"))

    def test_article_matches_number_with_suffix_accepts_when_text_has_matching_suffix(self) -> None:
        item = {"numeroArticulo": 167, "texto": "Artículo 167-B. Disposiciones especiales..."}
        self.assertTrue(mcp_server._article_matches_number(item, 167, suffix="B"))

    def test_infer_consulta_metadata_captures_suffix(self) -> None:
        metadata = mcp_server._infer_consulta_metadata(
            "artículo 167-B de la Ley Federal del Trabajo", "auto", None, None
        )
        self.assertEqual(metadata["numeroArticulo"], 167)
        self.assertEqual(metadata["articuloSufijo"], "B")

    def test_rank_laws_cache_populated_on_second_call(self) -> None:
        mcp_server._rank_laws_cache.clear()
        leyes = [{"nombre": "Constitucion"}, {"nombre": "Codigo Civil"}]
        mcp_server._rank_laws("constitucion", leyes)
        cache_size_after_first = len(mcp_server._rank_laws_cache)
        mcp_server._rank_laws("constitucion", leyes)
        self.assertEqual(len(mcp_server._rank_laws_cache), cache_size_after_first)

    def test_rank_laws_cache_returns_same_result(self) -> None:
        mcp_server._rank_laws_cache.clear()
        leyes = [{"nombre": "Constitucion"}, {"nombre": "Codigo Civil"}]
        result1 = mcp_server._rank_laws("constitucion", leyes)
        result2 = mcp_server._rank_laws("constitucion", leyes)
        self.assertEqual(result1, result2)


if __name__ == "__main__":
    unittest.main()
