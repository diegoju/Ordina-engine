# Ordina-engine

<p align="center">
  <img src="assets/ordina-logo.png" alt="Ordina logo" width="260" />
</p>

<p align="center">
  Consulta leyes, artículos, jurisprudencia y precedentes desde una sola capa.
</p>

Ordina-engine es una API y capa de consulta para información jurídica pública mexicana.

Está pensada para una idea simple: que encontrar leyes, artículos, jurisprudencia y precedentes sea más fácil de consultar, integrar y reutilizar.

Unifica principalmente:

- catálogo de leyes y ordenamientos;
- jurisprudencia del Semanario Judicial de la Federación (SJF);
- artículos legales consultados mediante Jurislex;
- precedentes y ejecutorias del buscador jurídico de la SCJN.

## Para qué sirve

Ordina sirve para:

- ubicar rápidamente una ley y sus identificadores de consulta;
- obtener artículos legales con un flujo más claro;
- buscar jurisprudencia del SJF y luego traer su detalle;
- consultar precedentes y ejecutorias de la SCJN desde una interfaz más uniforme;
- alimentar chats, GPTs, MCPs, scripts o aplicaciones jurídicas.

## Qué resuelve

Las fuentes jurídicas públicas existen, pero suelen tener uno o varios de estos problemas:

- interfaces cambiantes;
- respuestas poco uniformes;
- flujos distintos entre cada fuente;
- mayor dificultad para integrarlas en asistentes, buscadores o automatizaciones.

Ordina-engine funciona como una capa más uniforme para consultar esas fuentes sin tener que aprender un flujo distinto para cada una.

No intenta sustituir las fuentes oficiales. Intenta hacerlas más fáciles de consultar y conectar.

## Pruébalo rápido

Si quieres probarlo hoy mismo:

- Chat listo para usar: [Ordina Chat](https://chatgpt.com/g/g-67391c46cf708191929fd5baa1cbc010-ordina)
- Base URL de la API: `https://ordina-engine.vercel.app`
- Contrato principal: `openapi-ordina-hub.yaml`

Ordina puede usarse de tres formas:

1. como chat o GPT especializado;
2. como API HTTP;
3. como servidor MCP para Claude Desktop, Cursor, IDEs o agentes.

Qué obtienes con eso:

- una entrada más simple para consultar fuentes jurídicas públicas;
- un flujo más claro para leyes, artículos y jurisprudencia;
- una base reutilizable para asistentes, integraciones y automatizaciones.

Si vienes del mundo legal, lo más simple es empezar por el chat.

Si vienes del mundo técnico, empieza por `/ley`, `openapi-ordina-hub.yaml` o `mcp_server.py`.

## Flujo básico

La mayoría de consultas siguen este orden:

1. buscar una norma con `/normas/buscar` para saber si conviene seguir por `Jurislex`, `SIL` o ambas;
2. si la norma está en Jurislex, usar `idLegislacion` y `categoria` para artículos;
3. si la norma sólo está en SIL o quieres navegar el documento completo, usar `/legislacion/*`;
4. para jurisprudencia, buscar primero y luego consultar detalle por `ius`.

Ejemplos típicos:

- “dame el artículo 123 constitucional”;
- “busca jurisprudencia sobre amparo indirecto”;
- “encuentra el Código Penal de Oaxaca”;
- “localiza precedentes sobre libertad de expresión”.

Notas útiles:

- `idLegislacion` identifica la ley específica;
- `categoria` define la ruta de consulta en Jurislex;
- para evitar errores, primero consulta `/ley` y después usa esos valores en `/jurislex/articulos/*`;
- si el artículo tiene formato especial, por ejemplo `167-B`, conviene buscar primero por el número base.

## Endpoints principales

### Salud del servicio

- `GET /health`
- `GET /health/deep`

Ejemplo:

```json
{
  "status": "ok",
  "service": "Ordina-engine"
}
```

### Catálogo de leyes

Sirve para localizar una ley y obtener los identificadores que luego se usan en Jurislex.

Este suele ser el primer endpoint que debes usar.

- `GET /ley?id=<int>`
- `GET /ley?categoria=<int>`
- `GET /ley?nombre=<texto>`

Ejemplo:

```bash
curl --get "https://ordina-engine.vercel.app/ley" \
  --data-urlencode "nombre=constitución"
```

### Búsqueda unificada de normas

Este es el flujo recomendado para legislación cuando no sabes de antemano si una norma está mejor cubierta por Jurislex o por SIL.

- `GET /normas/buscar`
- `POST /normas/buscar`

Ejemplo:

```bash
curl --get "https://ordina-engine.vercel.app/normas/buscar" \
  --data-urlencode "nombre=ley de amparo"
```

La respuesta indica:

- si la norma existe en `jurislex`;
- si también existe en `sil`;
- cuál es la `rutaSugerida` para el siguiente paso.

### Búsqueda unificada de artículos

Si ya sabes el nombre de la norma y el artículo que necesitas, Ordina puede decidir automáticamente si consultar `Jurislex` o `SIL`.

- `GET /normas/articulos/buscar`
- `POST /normas/articulos/buscar`

Ejemplo por nombre de norma y artículo:

```bash
curl --get "https://ordina-engine.vercel.app/normas/articulos/buscar" \
  --data-urlencode "nombre=Ley de los Derechos de las Personas Adultas Mayores" \
  --data-urlencode "articulo=50"
```

Ejemplo con filtros SIL:

```bash
curl --get "https://ordina-engine.vercel.app/normas/articulos/buscar" \
  --data-urlencode "nombre=Ley General del Sistema de Medios de Impugnacion en Materia Electoral" \
  --data-urlencode "articulo=40" \
  --data-urlencode "categoriaOrdenamiento=LEY" \
  --data-urlencode "ambito=FEDERAL"
```

### Detalle unificado de artículo

Si quieres una sola respuesta homogénea con `libro`, `titulo`, `capitulo` y el texto del artículo, usa:

- `GET /normas/articulos/detalle`
- `POST /normas/articulos/detalle`

Ejemplos:

```bash
curl --get "https://ordina-engine.vercel.app/normas/articulos/detalle" \
  --data-urlencode "nombre=Ley de los Derechos de las Personas Adultas Mayores" \
  --data-urlencode "articulo=50"
```

```bash
curl --get "https://ordina-engine.vercel.app/normas/articulos/detalle" \
  --data-urlencode "nombre=Ley General del Sistema de Medios de Impugnacion en Materia Electoral" \
  --data-urlencode "articulo=40" \
  --data-urlencode "categoriaOrdenamiento=LEY" \
  --data-urlencode "ambito=FEDERAL"
```

### Jurisprudencia del SJF

Búsqueda:

- `GET /jurisprudencia/buscar?q=<término>&page=0&size=10`
- `POST /jurisprudencia/buscar`

Detalle:

- `GET /jurisprudencia/detalle?ius=<número>`

El detalle también soporta `debug=true` para revisar intentos y URLs cuando el upstream del SJF responde de forma inconsistente.

Ejemplo:

```bash
curl --get "https://ordina-engine.vercel.app/jurisprudencia/buscar" \
  --data-urlencode "q=amparo" \
  --data-urlencode "page=0" \
  --data-urlencode "size=3"
```

### Artículos legales en Jurislex

Para artículos, normalmente el flujo correcto es:

1. resolver la ley con `/ley`;
2. buscar coincidencias con `/jurislex/articulos/buscar`;
3. pedir detalle con `/jurislex/articulos/detalle`.

Búsqueda:

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

### Extracción de citas jurídicas

Ordina también puede analizar texto libre para detectar citas de artículos, leyes, jurisprudencia, tesis y registros digitales.

- `POST /citas/extraer`

Ejemplo:

```bash
curl -X POST "https://ordina-engine.vercel.app/citas/extraer" \
  -H "Content-Type: application/json" \
  -d '{
    "fuente": "demanda.txt",
    "texto": "Con fundamento en el artículo 14 de la Ley de Amparo y el artículo 16 constitucional, así como en la jurisprudencia 2a./J. 5/2020 y el registro digital 2023456.",
    "resolver": true
  }'
```

La landing principal también incluye un frontend mínimo para pegar texto o cargar archivos de texto y revisar las citas detectadas.

El extractor ahora también marca:

- `confianza`: qué tan sólida parece la identificación;
- `requiereConfirmacion`: cuándo conviene corroborar la cita detectada;
- `ius` y `rubro` cuando encuentra coincidencia exacta en SJF para claves como `P./J. 53/2026 (12a.)`.
- `textoCita` cuando se envía `resolver=true` y Ordina logra recuperar el contenido del artículo o criterio citado.

### Precedentes y ejecutorias de la SCJN

- `GET /precedentes/buscar`
- `POST /precedentes/buscar`

Ejemplo:

```bash
curl --get "https://ordina-engine.vercel.app/precedentes/buscar" \
  --data-urlencode "q=partido*" \
  --data-urlencode "indice=ejecutorias" \
  --data-urlencode "page=1" \
  --data-urlencode "size=3"
```

### Legislación SIL en el buscador jurídico SCJN

Sirve para consultar ordenamientos del índice de legislación del buscador jurídico de la SCJN, incluyendo filtros como ámbito, categoría y materia.

- `GET /legislacion/buscar`
- `POST /legislacion/buscar`
- `GET /legislacion/detalle`
- `GET /legislacion/articulos/buscar`

Ejemplo:

```bash
curl --get "https://ordina-engine.vercel.app/legislacion/buscar" \
  --data-urlencode "q=Electoral" \
  --data-urlencode "fuente=SIL" \
  --data-urlencode "indice=legislacion" \
  --data-urlencode "categoriaOrdenamiento=LEY" \
  --data-urlencode "ambito=FEDERAL" \
  --data-urlencode "page=1" \
  --data-urlencode "size=10"
```

Detalle de un ordenamiento concreto:

```bash
curl --get "https://ordina-engine.vercel.app/legislacion/detalle" \
  --data-urlencode "id=9006"
```

Buscar un artículo específico dentro del ordenamiento:

```bash
curl --get "https://ordina-engine.vercel.app/legislacion/articulos/buscar" \
  --data-urlencode "id=9006" \
  --data-urlencode "articulo=5"
```

También puedes combinar artículo y texto libre:

```bash
curl --get "https://ordina-engine.vercel.app/legislacion/articulos/buscar" \
  --data-urlencode "id=9006" \
  --data-urlencode "q=constitucionalidad"
```

## OpenAPI

El repositorio incluye varios contratos OpenAPI:

- `openapi-ordina-hub.yaml`: API completa recomendada;
- `openapi-sjf.yaml`: jurisprudencia y precedentes;
- `openapi-jurislex.yaml`: leyes, decretos y artículos;
- `openapi-legislaciones.yaml`: sólo catálogo de leyes.

Si quieres crear tu propio GPT o agente con Actions:

1. elige el archivo OpenAPI;
2. pégalo en tu integración;
3. verifica la base URL `https://ordina-engine.vercel.app`;
4. prueba al menos `GET /health` y `GET /ley?nombre=constitución`.

## Instrucciones para chats

Para obtener mejores resultados, usa las instrucciones base incluidas en `Ordina-instrucciones-minimas.md`.

Estas instrucciones ayudan a:

- seguir el flujo correcto entre endpoints;
- resolver primero la ley y después los artículos;
- buscar y explicar jurisprudencia con menos errores;
- mantener respuestas jurídicas claras y sin inventar información.

## Variables opcionales

Sólo son necesarias si alguna fuente bloquea solicitudes:

- `SJF_COOKIE`
- `JURISLEX_COOKIE`
- `BJ_SCJN_COOKIE`

## MCP

Ordina-engine también puede usarse como servidor MCP por `stdio` para clientes compatibles.

Esto te permite usar Ordina como backend jurídico para Claude Desktop u otros clientes MCP sin tener que reimplementar los flujos de leyes, artículos o jurisprudencia.

Configuración mínima:

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

Ejemplo de instalación local:

```bash
cd /ruta/a/tu/repo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python mcp_server.py
```

El MCP expone:

- herramientas base como `buscarLey`, `buscarJurisprudencia`, `buscarArticulosJurislex` y `obtenerDetalleArticuloJurislex`;
- herramientas compuestas como `resolverLeyPorNombre`, `buscarArticuloPorLeyYNumero`, `obtenerArticuloPorLeyYNumero` y `consultaJuridicaCompleta`;
- recursos y prompts para guiar a clientes compatibles.

Si necesitas despliegue remoto por SSH o HTTP, revisa `mcp_server.py` y `mcp_http_server.py` como referencia de implementación.

## Aviso importante

Ordina-engine y el chat oficial de Ordina son proyectos independientes y no oficiales.

Este proyecto:

- no está afiliado, patrocinado ni autorizado por ninguna institución pública;
- no busca replicar ni reemplazar servicios oficiales;
- no elude mecanismos de seguridad ni autenticación;
- sólo consume información públicamente accesible.

Toda verificación jurídica debe realizarse directamente en los portales institucionales correspondientes. Las personas usuarias son responsables del uso e interpretación de la información consultada mediante este proyecto.
