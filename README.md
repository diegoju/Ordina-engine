# Ordina-engine

Ordina-engine es una API que facilita la consulta de información jurídica pública mexicana desde un solo punto.

Su objetivo es simplificar el acceso técnico a:

- Leyes y catálogos normativos.
- Jurisprudencia del Semanario Judicial de la Federación (SJF).
- Artículos legales consultados vía Jurislex.

## ⚠️ Disclaimer

Ordina-engine y el chat oficial de Ordina son proyectos independientes y no oficiales.

Ordina-engine funciona como una capa técnica para facilitar la consulta de información jurídica pública disponible en sitios institucionales. El chat oficial utiliza esta API como interfaz de consulta, pero ninguno de los dos sustituye, modifica ni altera las fuentes oficiales.

Toda verificación jurídica debe realizarse directamente en los portales institucionales correspondientes.

Este proyecto:

- No está afiliado, patrocinado ni autorizado por ninguna institución pública.
- No busca replicar ni reemplazar servicios oficiales.
- No elude mecanismos de seguridad ni autenticación.
- Solo consume información públicamente accesible.

El uso de la API y del chat debe realizarse de forma responsable y respetando los términos de uso de las fuentes originales.

Los usuarios son responsables del uso e interpretación de la información consultada mediante este proyecto.

## 🚀 Uso rápido (30 segundos)

Si solo quieres probarlo:

GPT oficial:

➡️ Próximamente enlace oficial de Ordina

Base URL de la API:

`https://ordina-engine.vercel.app`

## 📌 ¿Qué problema resuelve?

Las fuentes jurídicas públicas existen, pero:

- sus interfaces pueden cambiar;
- algunas respuestas son inconsistentes;
- la integración técnica suele ser compleja.

Ordina-engine unifica esos servicios bajo un esquema más estable y fácil de integrar en:

- asistentes de IA,
- herramientas legales,
- scripts de automatización,
- sistemas de búsqueda.

## 🧭 Flujo básico (cómo se usa realmente)

La mayoría de consultas siguen este orden:

1. Buscar una ley -> obtener `idLegislacion` y `categoria`.
2. Buscar jurisprudencia por término (opcional).
3. Consultar artículos usando esos identificadores.

### 🔎 Nota sobre `idLegislacion` y `categoria`

- `idLegislacion` identifica la ley específica.
- `categoria` define la ruta de consulta en Jurislex.
- Para evitar errores, primero consulta `/ley` y luego usa esos valores en `/jurislex/articulos/*`.
- Si el artículo tiene formato especial (por ejemplo `167-B`), busca primero por número base (`167`) y después selecciona el resultado correcto.

## 📡 Endpoints principales

### 1️⃣ Estado del servicio

- `GET /health`
- `GET /health/deep` (valida catálogo + SJF + Jurislex)

Respuesta esperada:

```json
{
  "status": "ok",
  "service": "Ordina-engine"
}
```

`/health/deep` devuelve `ok` o `degraded` según el estado de dependencias externas.

### 2️⃣ Catálogo de leyes

Permite localizar la ley y obtener sus identificadores.

- `GET /ley?id=<int>`
- `GET /ley?categoria=<int>`
- `GET /ley?nombre=<texto>`

Ejemplo:

```bash
curl --get "https://ordina-engine.vercel.app/ley" \
  --data-urlencode "nombre=constitución"
```

### 3️⃣ Jurisprudencia (SJF)

Buscar:

- `GET /jurisprudencia/buscar?q=<termino>&page=0&size=10`
- `POST /jurisprudencia/buscar`

Detalle:

- `GET /jurisprudencia/detalle?ius=<numero>`

Ejemplo:

```bash
curl --get "https://ordina-engine.vercel.app/jurisprudencia/buscar" \
  --data-urlencode "q=amparo" \
  --data-urlencode "page=0" \
  --data-urlencode "size=3"
```

### 4️⃣ Artículos legales (Jurislex)

Buscar artículos:

- `GET /jurislex/articulos/buscar`
- `POST /jurislex/articulos/buscar`

Detalle:

- `GET /jurislex/articulos/detalle`

Ejemplo:

```bash
curl --get "https://ordina-engine.vercel.app/jurislex/articulos/buscar" \
  --data-urlencode "categoria=1000" \
  --data-urlencode "idLegislacion=1000" \
  --data-urlencode "soloArticulo=true"
```

## 📂 Esquemas OpenAPI incluidos

Selecciona según lo que necesites:

- `openapi-ordina-hub.yaml` -> todo en uno (recomendado).
- `openapi-sjf.yaml` -> solo jurisprudencia.
- `openapi-jurislex.yaml` -> artículos + leyes.
- `openapi-legislaciones.yaml` -> solo catálogo de leyes.

## 🧠 Crear tu propio GPT o agente

### Opción rápida

Usar el GPT existente:

Próximamente enlace oficial de Ordina.

### Opción personalizada (Actions)

1. Elige un archivo OpenAPI.
2. Ve a Actions en tu GPT.
3. Pega el YAML.
4. Verifica base URL:
   - `https://ordina-engine.vercel.app`
5. Prueba:
   - `GET /health`
   - `GET /ley?nombre=constitución`

## Instrucciones del chat (recomendado)

Para obtener mejores resultados, utiliza las instrucciones base incluidas en este repositorio.

1. Abre el archivo:

   `Ordina-instrucciones-minimas.md`

2. Copia su contenido completo.

3. Pégalo en la sección **Instructions** (o **System Instructions**) de tu chat o GPT personalizado.

Estas instrucciones definen:

- el flujo correcto de consulta entre endpoints,
- cómo resolver leyes -> artículos,
- cómo buscar y explicar jurisprudencia,
- y el comportamiento esperado del asistente al usar la API.

Sin estas instrucciones, el chat puede realizar consultas incompletas o usar los endpoints de forma incorrecta.

## ⚙️ Variables opcionales

Solo necesarias si alguna fuente bloquea solicitudes:

- `SJF_COOKIE`
- `JURISLEX_COOKIE`

## ✅ Verificación automática

El repositorio incluye un workflow de GitHub Actions (`.github/workflows/smoke-tests.yml`) que ejecuta `smoke_test.py` en cada push a `main`.
