# IdLegislaciones.json — Schema Reference

The catalog contains **1,255 laws** from Mexican federal and state legal sources.
It is loaded into memory at startup and used for law name resolution.

## Record structure

```json
{
  "categoria": 2000,
  "id": 2047,
  "nombre": "Código Civil para el Estado de Jalisco"
}
```

| Field | Type | Description |
|---|---|---|
| `categoria` | `integer` | Jurislex category ID. Determines which Jurislex endpoint handles this law. Pass as `categoria` to all Jurislex API calls. |
| `id` | `integer` | Jurislex legislation ID (`idLegislacion`). Unique per law. Pass as `idLegislacion` to Jurislex calls. |
| `nombre` | `string` | Full official name of the law. Used for fuzzy name matching. |

> **Note**: For the root law of a category (e.g. `Código Civil Federal`), `categoria == id`.
> For derived or related laws (state codes, protocols), `id` differs from `categoria` but shares the same `categoria` group.

## Category groups

| `categoria` | Root law | Laws in group |
|---|---|---|
| `1000` | Constitución Política de los Estados Unidos Mexicanos | 36 |
| `1100` | Ley de Amparo | 5 |
| `2000` | Código Civil Federal | 268 |
| `3000` | Código Fiscal de la Federación | 162 |
| `4000` | Ley Federal del Trabajo | 121 |
| `5000` | Código de Comercio | 44 |
| `6000` | Código Penal Federal | 302 |
| `8000` | Ley Federal del Derecho de Autor | 46 |
| *(others)* | Various | remaining |

## How name resolution works

The `_rank_laws(nombre, leyes)` function in `mcp_server.py` scores each catalog entry
against the query using a 5-level scoring system:

1. Exact normalized match
2. Law name starts with the query
3. Whole query appears as a word boundary
4. All query tokens present
5. Query is a substring

Results are sorted by score ascending (best first) and the top N are used
for Jurislex queries. Results are cached in `_rank_laws_cache` keyed on
`(normalized_name, catalog_size)`.
