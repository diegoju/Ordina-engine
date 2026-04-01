# Ordina-engine

<p align="center">
  <img src="assets/ordina-logo.png" alt="Ordina logo" width="260" />
</p>

Ordina-engine es una API para consultar informacion juridica publica mexicana desde un solo punto.

Esta pensada para dos tipos de uso:

- personas tecnicas que quieren integrar fuentes juridicas en aplicaciones, asistentes o scripts;
- personas del ambito legal que necesitan entender con claridad que puede consultar el sistema y como se usa.

Ordina-engine unifica principalmente:

- catalogo de leyes y ordenamientos;
- jurisprudencia del Semanario Judicial de la Federacion (SJF);
- articulos legales consultados mediante Jurislex;
- precedentes y ejecutorias del buscador juridico de la SCJN.

## Aviso importante

Ordina-engine y el chat oficial de Ordina son proyectos independientes y no oficiales.

Ordina-engine funciona como una capa tecnica para facilitar la consulta de informacion juridica publica disponible en sitios institucionales. El chat oficial usa esta API como interfaz de consulta, pero ninguno de los dos sustituye, modifica ni altera las fuentes oficiales.

Este proyecto:

- no esta afiliado, patrocinado ni autorizado por ninguna institucion publica;
- no busca replicar ni reemplazar servicios oficiales;
- no elude mecanismos de seguridad ni autenticacion;
- solo consume informacion publicamente accesible.

Toda verificacion juridica debe realizarse directamente en los portales institucionales correspondientes. Las personas usuarias son responsables del uso e interpretacion de la informacion consultada mediante este proyecto.

## Uso rapido

Si solo quieres probarlo:

- Chat listo para usar: [Ordina Chat](https://chatgpt.com/g/g-67391c46cf708191929fd5baa1cbc010-ordina)
- Base URL de la API: `https://ordina-engine.vercel.app`

## Que problema resuelve

Las fuentes juridicas publicas existen, pero normalmente presentan una o varias de estas dificultades:

- interfaces que cambian con el tiempo;
- respuestas inconsistentes o poco amigables para integracion;
- flujos tecnicos distintos entre cada fuente;
- mayor complejidad para construir asistentes, buscadores o automatizaciones.

Ordina-engine ofrece una capa mas uniforme para consultar esas fuentes con un contrato mas estable.

## Flujo basico de uso

La mayoria de consultas siguen este orden:

1. Buscar una ley para obtener `idLegislacion` y `categoria`.
2. Si hace falta, buscar jurisprudencia por termino.
3. Consultar articulos usando los identificadores obtenidos.
4. Si se requiere texto completo, pedir el detalle del articulo o de la jurisprudencia.

### Sobre `idLegislacion` y `categoria`

- `idLegislacion` identifica la ley especifica.
- `categoria` define la ruta de consulta en Jurislex.
- Para evitar errores, primero consulta `/ley` y luego usa esos valores en `/jurislex/articulos/*`.
- Si el articulo tiene formato especial, por ejemplo `167-B`, conviene buscar primero por el numero base, por ejemplo `167`, y luego elegir el resultado correcto.

## Endpoints principales

### Estado del servicio

- `GET /health`
- `GET /health/deep`

`/health` devuelve el estado basico del servicio.

Ejemplo de respuesta:

```json
{
  "status": "ok",
  "service": "Ordina-engine"
}
```

`/health/deep` revisa dependencias clave como catalogo local, SJF y Jurislex, y devuelve `ok` o `degraded`.

### Catalogo de leyes

Permite localizar una ley y obtener los identificadores que despues se usan en Jurislex.

- `GET /ley?id=<int>`
- `GET /ley?categoria=<int>`
- `GET /ley?nombre=<texto>`

Ejemplo:

```bash
curl --get "https://ordina-engine.vercel.app/ley" \
  --data-urlencode "nombre=constitucion"
```

### Jurisprudencia del SJF

Busqueda:

- `GET /jurisprudencia/buscar?q=<termino>&page=0&size=10`
- `POST /jurisprudencia/buscar`

Detalle:

- `GET /jurisprudencia/detalle?ius=<numero>`

El detalle tambien soporta `debug=true` para revisar intentos y URLs cuando el upstream del SJF responde de forma inconsistente.

Ejemplo:

```bash
curl --get "https://ordina-engine.vercel.app/jurisprudencia/buscar" \
  --data-urlencode "q=amparo" \
  --data-urlencode "page=0" \
  --data-urlencode "size=3"
```

### Articulos legales en Jurislex

Busqueda:

- `GET /jurislex/articulos/buscar`
- `POST /jurislex/articulos/buscar`

Detalle:

- `GET /jurislex/articulos/detalle`

Decretos y anexos:

- `GET /jurislex/decretos`

Ejemplo:

```bash
curl --get "https://ordina-engine.vercel.app/jurislex/articulos/buscar" \
  --data-urlencode "categoria=1000" \
  --data-urlencode "idLegislacion=1000" \
  --data-urlencode "soloArticulo=true"
```

### Precedentes y ejecutorias de la SCJN

Busqueda:

- `GET /precedentes/buscar`
- `POST /precedentes/buscar`

La respuesta resumida evita el bloque pesado de embeddings y devuelve un formato mas facil de consumir.

Ejemplo:

```bash
curl --get "https://ordina-engine.vercel.app/precedentes/buscar" \
  --data-urlencode "q=partido*" \
  --data-urlencode "indice=ejecutorias" \
  --data-urlencode "page=1" \
  --data-urlencode "size=3"
```

## Esquemas OpenAPI incluidos

Usa el archivo que mejor se ajuste a tu integracion:

- `openapi-ordina-hub.yaml`: contrato principal con la API completa;
- `openapi-sjf.yaml`: solo jurisprudencia y precedentes;
- `openapi-jurislex.yaml`: leyes, decretos y articulos;
- `openapi-legislaciones.yaml`: solo catalogo de leyes.

## Crear tu propio GPT o agente

### Opcion rapida

Usa el GPT existente:

[Ordina Chat](https://chatgpt.com/g/g-67391c46cf708191929fd5baa1cbc010-ordina)

### Opcion personalizada con Actions

1. Elige un archivo OpenAPI.
2. Ve a Actions en tu GPT.
3. Pega el YAML.
4. Verifica la base URL: `https://ordina-engine.vercel.app`
5. Prueba al menos:
   - `GET /health`
   - `GET /ley?nombre=constitucion`

## Instrucciones del chat

Para obtener mejores resultados, usa las instrucciones base incluidas en este repositorio.

1. Abre `Ordina-instrucciones-minimas.md`.
2. Copia su contenido completo.
3. Pegalo en la seccion `Instructions` o `System Instructions` de tu chat o GPT personalizado.

Estas instrucciones ayudan a:

- seguir el flujo correcto entre endpoints;
- resolver primero la ley y despues los articulos;
- buscar y explicar jurisprudencia con menos errores;
- mantener respuestas juridicas claras y sin inventar informacion.

## Variables opcionales

Solo son necesarias si alguna fuente bloquea solicitudes:

- `SJF_COOKIE`
- `JURISLEX_COOKIE`
- `BJ_SCJN_COOKIE`

## Servidor MCP

Ordina-engine tambien puede usarse como servidor MCP por `stdio` para clientes compatibles, por ejemplo Claude Desktop, Cursor, IDEs o agentes.

### Ejecutar localmente

En todos los sistemas el MCP actual corre por `stdio`. Eso significa que tu cliente inicia `mcp_server.py` como proceso local y se comunica por entrada y salida estandar.

### macOS

```bash
cd /ruta/a/tu/repo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python mcp_server.py
```

Ejemplo para Claude Desktop en macOS:

```json
{
  "mcpServers": {
    "ordina-engine": {
      "command": "/ruta/a/tu/repo/.venv/bin/python",
      "args": ["/ruta/a/tu/repo/mcp_server.py"]
    }
  }
}
```

### Linux

```bash
cd /ruta/a/tu/repo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python mcp_server.py
```

Ejemplo para Claude Desktop en Linux:

```json
{
  "mcpServers": {
    "ordina-engine": {
      "command": "/ruta/a/tu/repo/.venv/bin/python",
      "args": ["/ruta/a/tu/repo/mcp_server.py"]
    }
  }
}
```

### Windows

En Windows conviene usar rutas absolutas y el `python.exe` del `venv`.

```powershell
cd C:\ruta\a\tu\repo
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python mcp_server.py
```

Ejemplo para Claude Desktop en Windows:

```json
{
  "mcpServers": {
    "ordina-engine": {
      "command": "C:\\ruta\\a\\tu\\repo\\.venv\\Scripts\\python.exe",
      "args": ["C:\\ruta\\a\\tu\\repo\\mcp_server.py"]
    }
  }
}
```

Notas practicas para cualquier sistema:

- usa rutas absolutas en `command` y `args`;
- prueba primero que `mcp_server.py` arranque manualmente;
- si necesitas cookies opcionales, defínelas en el entorno donde corre el proceso;
- para clientes GUI, suele ser mas estable usar el `python` del `venv` que depender del PATH del sistema.

### Despliegue personal en servidor Linux

Mientras el MCP siga corriendo por `stdio`, la forma mas simple de usarlo desde otra maquina es instalarlo en tu servidor y hacer que Claude lo arranque por `ssh` bajo demanda. No hace falta exponer puertos HTTP ni dejar un servicio MCP publico todavia.

Ejemplo en Arch Linux:

```bash
sudo pacman -Syu --noconfirm git python python-pip
git clone https://github.com/<tu-usuario>/<tu-repo>.git /opt/ordina-engine
cd /opt/ordina-engine/repo
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m py_compile mcp_server.py api.py
python -m unittest test_mcp_server.py
```

Si vas a usar cookies opcionales para SJF, Jurislex o SCJN, exportalas en el entorno del usuario remoto que ejecutara `python`.

### Usar Claude Desktop contra un servidor remoto por SSH

Este flujo sigue siendo MCP por `stdio`, pero el proceso corre en tu servidor remoto en vez de tu laptop. Claude Desktop abre `ssh`, inicia `mcp_server.py` y habla con el proceso a traves del canal estandar.

Pasos recomendados:

1. Configura acceso por llave SSH sin password entre tu maquina local y el servidor.
2. Verifica manualmente que este comando funcione desde tu maquina local.
3. Usa la misma ruta absoluta en la configuracion de Claude Desktop.

Prueba manual:

```bash
ssh usuario@tu-servidor '/opt/ordina-engine/repo/.venv/bin/python /opt/ordina-engine/repo/mcp_server.py'
```

Ejemplo para Claude Desktop usando SSH:

```json
{
  "mcpServers": {
    "ordina-engine": {
      "command": "ssh",
      "args": [
        "usuario@tu-servidor",
        "/opt/ordina-engine/repo/.venv/bin/python /opt/ordina-engine/repo/mcp_server.py"
      ]
    }
  }
}
```

Notas practicas:

- usa rutas absolutas en el servidor remoto;
- normalmente conviene usar el `python` del `venv` en vez del sistema;
- si `ssh` pide password o confirmacion de host, resuelvelo antes fuera de Claude;
- este esquema es suficiente para uso personal o adopcion temprana.

### MCP remoto por HTTP

Si quieres una URL para usar el conector remoto de Claude, el repositorio incluye `mcp_http_server.py`, que expone un endpoint MCP por Streamable HTTP en `/mcp`.

Arranque local rapido:

```bash
uvicorn mcp_http_server:app --host 127.0.0.1 --port 8000
```

Endpoint MCP remoto:

```text
http://127.0.0.1:8000/mcp
```

Notas del servidor HTTP:

- `POST /mcp` procesa mensajes JSON-RPC MCP y devuelve `application/json`;
- `GET /mcp` responde `405` porque esta version minima no abre stream SSE separado;
- `DELETE /mcp` cierra una sesion MCP activa;
- en `initialize`, el servidor devuelve `Mcp-Session-Id` y espera ese header en llamadas posteriores;
- si defines `MCP_ALLOWED_ORIGINS`, solo aceptara esos `Origin` separados por comas.

### Despliegue remoto en Arch Linux con Caddy

Ejemplo de arranque del servidor HTTP en tu VPS o servidor personal:

```bash
cd /home/mou/Ordina-engine
./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn mcp_http_server:app --host 127.0.0.1 --port 8000
```

Si quieres dejarlo como servicio, puedes crear un `systemd` unit sencillo:

```ini
[Unit]
Description=Ordina MCP HTTP
After=network.target

[Service]
User=mou
WorkingDirectory=/home/mou/Ordina-engine
Environment=MCP_ALLOWED_ORIGINS=https://claude.ai,https://claude.ai/settings/connectors
ExecStart=/home/mou/Ordina-engine/.venv/bin/uvicorn mcp_http_server:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Con Caddy al frente:

```caddy
mcp.tu-dominio.com {
    reverse_proxy 127.0.0.1:8000
}
```

La URL final para Claude quedaria asi:

```text
https://mcp.tu-dominio.com/mcp
```

### Configuracion remota para Claude

Si ya tienes la URL HTTPS publicada, puedes agregar el conector remoto de Claude usando:

```json
{
  "mcpServers": {
    "ordina-engine-remote": {
      "type": "http",
      "url": "https://mcp.tu-dominio.com/mcp"
    }
  }
}
```

### Que expone el MCP

El servidor MCP tiene tres capas.

Herramientas base para reflejar la API:

- `health`
- `healthDeep`
- `buscarLey`
- `buscarJurisprudencia`
- `buscarJurisprudenciaAvanzada`
- `obtenerDetalleJurisprudencia`
- `buscarPrecedentes`
- `buscarPrecedentesAvanzado`
- `buscarDecretosJurislex`
- `buscarArticulosJurislex`
- `buscarArticulosJurislexAvanzado`
- `obtenerDetalleArticuloJurislex`

Herramientas compuestas para flujos mas utiles en asistentes:

- `resolverLeyPorNombre`
- `buscarYDetallarJurisprudencia`
- `buscarArticuloPorLeyYNumero`
- `obtenerArticuloPorLeyYNumero`
- `consultaJuridicaCompleta`

Recursos y prompts MCP para guiar a clientes compatibles:

- resources: `ordina://readme`, `ordina://instrucciones-minimas`, `ordina://openapi/hub`, `ordina://catalogo/preview`
- prompts: `consulta-juridica-segura`, `buscar-articulo`, `buscar-jurisprudencia`

### Tools compuestas mas importantes

`obtenerArticuloPorLeyYNumero` resuelve la ley por nombre, localiza el articulo exacto y devuelve tambien el detalle completo.

`consultaJuridicaCompleta` intenta decidir si la consulta debe resolverse como ley, articulo, jurisprudencia o precedente. Ademas:

- intenta inferir `numeroArticulo` desde texto libre;
- intenta detectar una pista de `nombreLey` dentro de la consulta;
- devuelve `summary`, `confidence` y `reasons` para explicar la estrategia elegida.

### Resources MCP disponibles

- `ordina://readme`
- `ordina://instrucciones-minimas`
- `ordina://openapi/hub`
- `ordina://catalogo/preview`

### Prompts MCP disponibles

- `consulta-juridica-segura`
- `buscar-articulo`
- `buscar-jurisprudencia`

### Configuracion de ejemplo

Configuracion minima:

```json
{
  "command": "python3",
  "args": ["/ruta/a/tu/repo/mcp_server.py"]
}
```

Ejemplo para Claude Desktop:

```json
{
  "mcpServers": {
    "ordina-engine": {
      "command": "python3",
      "args": ["/ruta/a/tu/repo/mcp_server.py"]
    }
  }
}
```

Ejemplo para Cursor:

```json
{
  "mcpServers": {
    "ordina-engine": {
      "command": "python3",
      "args": ["/ruta/a/tu/repo/mcp_server.py"]
    }
  }
}
```

### Verificacion del MCP

```bash
python -m py_compile mcp_server.py api.py
python -m unittest test_mcp_server.py
```

La suite MCP cubre:

- dispatcher puro;
- errores y edge cases con mocks;
- integracion real por `stdio` contra `mcp_server.py`.

### Cliente de ejemplo

`mcp_client_example.py` muestra un cliente minimo que:

- inicia el servidor MCP como subprocess;
- ejecuta `initialize`;
- llama la tool `consultaJuridicaCompleta`;
- imprime la respuesta JSON-RPC completa.

Ejecutalo asi:

```bash
python mcp_client_example.py
```

## Verificacion automatica

El repositorio incluye un workflow de GitHub Actions en `.github/workflows/smoke-tests.yml` que ejecuta `smoke_test.py` en cada push a `main`.
