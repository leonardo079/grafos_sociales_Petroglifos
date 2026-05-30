# Arquitectura Técnica — Grafos Sociales Rupestres

Sistema de análisis de similitud iconográfica entre sitios rupestres colombianos. Convierte imágenes de petroglifos en vectores de alta dimensión, los almacena en una base de datos vectorial y construye un grafo social ponderado donde cada arista representa similitud estilística comprobada entre dos sitios arqueológicos.

---

## Índice

1. [Visión general y arquitectura](#1-visión-general-y-arquitectura)
2. [Configuración](#2-configuración)
3. [Capa de base de datos](#3-capa-de-base-de-datos)
4. [Dominio](#4-dominio)
5. [Adaptadores de salida](#5-adaptadores-de-salida)
6. [Orquestador — pipeline principal](#6-orquestador--pipeline-principal)
7. [Grafo social en memoria](#7-grafo-social-en-memoria)
8. [API REST](#8-api-rest)
9. [Scripts de operaciones](#9-scripts-de-operaciones)
10. [Sistema de confianza de aristas](#10-sistema-de-confianza-de-aristas)
11. [Flujo de datos completo](#11-flujo-de-datos-completo)
12. [Esquema de la base de datos](#12-esquema-de-la-base-de-datos)

---

## 1. Visión general y arquitectura

El sistema sigue **arquitectura hexagonal** (puertos y adaptadores): el núcleo de dominio y la lógica de negocio no dependen de infraestructura concreta; los detalles externos (base de datos, modelos de ML, API HTTP) se enchufan como adaptadores.

```
┌─────────────────────────────────────────────────────────────┐
│  Adaptadores de entrada                                     │
│  adapters/inbound/api/main.py  ←  HTTP / FastAPI            │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│  Orquestador  (orchestrator/comparator.py)                  │
│  Coordina el pipeline completo de una comparación           │
└──────┬──────────────────────────────────┬───────────────────┘
       │                                  │
┌──────▼──────┐                  ┌────────▼────────────────────┐
│  Dominio    │                  │  Grafo social               │
│  core/      │                  │  graphs/social_graph.py     │
└─────────────┘                  └─────────────────────────────┘
       │                                  │
┌──────▼──────────────────────────────────▼───────────────────┐
│  Adaptadores de salida                                      │
│  ┌────────────────────────┐  ┌────────────────────────────┐ │
│  │  EfficientNet-B0       │  │  PostgreSQL + pgvector     │ │
│  │  (embeddings)          │  │  (Supabase)                │ │
│  └────────────────────────┘  └────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Estructura de directorios

```
grafos_sociales/
├── adapters/
│   ├── inbound/api/main.py          # FastAPI — todos los endpoints
│   └── outbound/
│       ├── embeddings/              # EfficientNet-B0
│       └── vector_store/            # pgvector (búsqueda coseno)
├── config/
│   └── settings.py                  # Pydantic-settings desde .env
├── core/
│   └── domain/
│       ├── entities/site.py         # Dataclasses de dominio
│       └── enums/taxonomy.py        # Vocabulario controlado
├── graphs/
│   └── social_graph.py              # NetworkX + análisis + exportación
├── infrastructure/
│   └── database/
│       ├── models/models.py         # ORM SQLAlchemy
│       ├── migrations/schema.sql    # DDL completo
│       └── session.py               # Engine async + get_session
├── orchestrator/
│   └── comparator.py                # Pipeline principal
└── scripts/
    ├── bulk_compare.py              # Procesamiento en lote
    ├── migrate.py                   # Crear tablas desde schema.sql
    ├── migrate_add_provisional.py   # Migración incremental is_provisional
    └── seed_embeddings.py           # Carga del corpus de referencia
```

---

## 2. Configuración

**Archivo:** `config/settings.py`

Usa `pydantic-settings` para leer variables desde `.env` y exponerlas como un objeto tipado accesible en todo el proyecto mediante `settings`.

### Variables disponibles

| Variable | Tipo | Default | Descripción |
|---|---|---|---|
| `DATABASE_URL` | str | — | URL async (`asyncpg`) — se deriva automáticamente de la sync |
| `DATABASE_URL_SYNC` | str | — | URL psycopg2 al pooler de Supabase (IPv4) |
| `IMAGE_TOP_K` | int | 5 | Máximo de resultados por búsqueda vectorial |
| `IMAGE_MIN_SIMILARITY` | float | 0.60 | Similitud mínima para devolver un match |
| `EDGE_MIN_SIMILARITY` | float | 0.70 | Similitud mínima para crear una arista |
| `EDGE_RELIABLE_MIN_SIMILARITY` | float | 0.76 | Umbral alto del doble criterio de confiabilidad |
| `EDGE_MIN_EVIDENCE` | int | 2 | Evidencias mínimas para arista confiable |
| `ENV` | str | `development` | Activa SQL echo si es `development` |
| `LOG_LEVEL` | str | `INFO` | Nivel de logs structlog |

### Corrección de driver (validador)

El método `_fix_db_driver` se ejecuta automáticamente tras la carga. Supabase expone dos hosts:
- El host directo (`db.*.supabase.co:5432`) solo resuelve a IPv6 — no funciona en todas las redes.
- El pooler (`*.pooler.supabase.com:6543`) tiene IPv4 y es más robusto.

El validador toma `DATABASE_URL_SYNC` (pooler con psycopg2), reemplaza el driver a `asyncpg` y lo usa como `DATABASE_URL`. Así ambas URLs apuntan siempre al pooler.

### Singleton con caché

```python
@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()  # instancia global
```

`@lru_cache` garantiza que `.env` se lee una sola vez por proceso.

---

## 3. Capa de base de datos

### 3.1 Sesión (`infrastructure/database/session.py`)

Crea el engine async de SQLAlchemy y el `sessionmaker`:

```
create_async_engine
├── pool_pre_ping=True       → descarta conexiones rotas antes de usarlas
├── pool_size=5              → conexiones base
├── max_overflow=10          → hasta 15 en picos
└── statement_cache_size=0   → requerido por PgBouncer (Supabase usa pooling)
```

`get_session()` es un generador async usado como dependencia FastAPI:
1. Abre sesión.
2. Hace `yield` (FastAPI inyecta la sesión en el handler).
3. Si el handler termina sin errores → `commit()`.
4. Si hay excepción → `rollback()` y re-lanza.

### 3.2 Modelos ORM (`infrastructure/database/models/models.py`)

#### `RupestranSiteModel` — tabla `rupestrian_sites`

Representa un **nodo** del grafo: un sitio arqueológico con petroglifos.

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | UUID PK | Identificador único auto-generado |
| `name` | String(255) UNIQUE | Nombre del sitio (ej. "Villa de Leyva") |
| `municipality` | String(255) | Municipio |
| `department` | String(255) | Departamento colombiano |
| `latitude` / `longitude` | Float nullable | Coordenadas geográficas |
| `conservation_status` | String(50) | Estado de conservación |
| `dominant_taxonomy` | String(100) | Categoría iconográfica predominante |
| `petroglyph_count` | Integer | Número de motivos registrados |
| `metadata_` | JSONB | Datos extras sin esquema fijo |
| `created_at` | DateTime | Timestamp de inserción |

#### `ImageEmbedding` — tabla `image_embeddings`

Corpus de referencia para búsqueda vectorial. Cada fila = una imagen de petroglifo ya procesada.

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | UUID PK | — |
| `petroglyph_id` | String nullable | Referencia cruzada (sin FK explícita) |
| `site_name` | String | Nombre del sitio origen |
| `municipality` | String | Municipio del sitio |
| `reference_name` | String | Nombre descriptivo del motivo |
| `taxonomy` | String | Categoría (Geométrico, Zoomorfo…) |
| `image_path` | Text | Ruta local de la imagen |
| `embedding` | `Vector(1280)` | Vector EfficientNet-B0 |
| `metadata_` | JSONB | Datos adicionales |

Índice `ivfflat` sobre `embedding` con `vector_cosine_ops`: acelera la búsqueda de vecinos más cercanos usando similitud coseno. Se recomienda crearlo solo después de tener ≥ 100 filas para que IVFFlat pueda calcular centroides útiles.

#### `SiteGraphEdge` — tabla `site_graph_edges`

Representa una **arista** del grafo social: similitud iconográfica entre dos sitios.

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | UUID PK | — |
| `site_a_id` | UUID FK | Primer sitio (siempre el menor UUID en orden lexicográfico) |
| `site_b_id` | UUID FK | Segundo sitio |
| `weight` | Float | Similitud coseno promedio acumulada (0–1) |
| `shared_taxonomies` | JSONB array | Taxonomías de los motivos que aportaron evidencia |
| `evidence_count` | Integer | Número de comparaciones que confirmaron la arista |
| `is_provisional` | Boolean | `True` si no cumple el doble criterio de confiabilidad |
| `created_at` | DateTime | Timestamp de primera creación |
| `updated_at` | DateTime | Timestamp de última actualización (trigger automático) |

Constraint `UNIQUE (site_a_id, site_b_id)` garantiza que la arista entre dos sitios es única. Los IDs siempre se insertan en orden lexicográfico (`sorted([id_a, id_b])`) para evitar duplicados invertidos.

### 3.3 Esquema DDL (`infrastructure/database/migrations/schema.sql`)

Script SQL ejecutado una sola vez contra Supabase para crear las tres tablas y un trigger:

```sql
-- Trigger: actualiza updated_at automáticamente en cada UPDATE de site_graph_edges
CREATE OR REPLACE TRIGGER trg_site_graph_edges_updated_at
    BEFORE UPDATE ON site_graph_edges
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
```

---

## 4. Dominio

### 4.1 Entidades (`core/domain/entities/site.py`)

Dataclasses Python puras que modelan conceptos del dominio sin dependencias de infraestructura:

- **`GeoLocation`**: latitud, longitud, altitud opcional, precisión GPS opcional.
- **`RupestranSite`**: nodo del grafo con todos sus atributos + lista de sitios similares.

Estas entidades son independientes del ORM y pueden usarse en lógica de dominio sin acceder a la BD.

### 4.2 Vocabulario controlado (`core/domain/enums/taxonomy.py`)

#### `TaxonomyCategory` (StrEnum)

Clasifica los motivos rupestres en 7 categorías:

| Valor | Descripción |
|---|---|
| `Antropomorfo` | Representaciones de figura humana |
| `Zoomorfo` | Animales o figuras zoomórficas |
| `Geométrico` | Formas abstractas, espirales, líneas |
| `Astronómico` | Soles, lunas, constelaciones |
| `Fitomorfo` | Plantas o formas vegetales |
| `Híbrido` | Combinaciones de categorías |
| `Indeterminado` | Sin clasificar (default) |

`from_str()` hace lookup case-insensitive y cae a `Indeterminado` si no hay match.

#### `ConservationStatus` (StrEnum)

Cinco estados: `Bueno`, `Regular`, `Malo`, `Crítico`, `Perdido`.

---

## 5. Adaptadores de salida

### 5.1 EfficientNet-B0 (`adapters/outbound/embeddings/efficientnet_adapter.py`)

Extrae un vector de características de 1280 dimensiones a partir de una imagen.

#### Carga del modelo (al importar el módulo)

```python
import timm
model = timm.create_model("efficientnet_b0", pretrained=True, num_classes=0)
model.eval()
```

- `pretrained=True`: pesos ImageNet preentrenados.
- `num_classes=0`: elimina la cabeza de clasificación; la salida es el vector de características global (Global Average Pooling de la última capa convolucional).
- `model.eval()`: desactiva dropout y batch norm en modo inferencia.

Si `timm` no está instalado o el modelo no puede cargarse, `_MODEL = None` y todas las llamadas retornan `None` silenciosamente.

#### Pipeline de transformación

Cada imagen pasa por:
1. `Resize(224, 224)` — tamaño esperado por EfficientNet.
2. `ToTensor()` — convierte PIL Image a tensor `[C, H, W]` normalizado a `[0, 1]`.
3. `Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])` — media y desviación estándar de ImageNet.

#### `extract_image_embedding(image_path)`

1. Verifica que `_MODEL` no sea `None` y que el archivo exista.
2. Abre la imagen como RGB (elimina transparencia alpha si existe).
3. Aplica las transformaciones y añade dimensión batch: tensor `[1, 3, 224, 224]`.
4. Ejecuta inferencia con `torch.no_grad()` (sin calcular gradientes → más rápido y menos memoria).
5. `features.squeeze()` elimina la dimensión batch → vector `[1280]`.
6. Convierte a lista de Python con `.numpy().tolist()` para serialización.
7. Retorna `None` ante cualquier excepción.

### 5.2 pgvector (`adapters/outbound/vector_store/pgvector_adapter.py`)

Clase `ImageVectorAdapter` que encapsula búsquedas de similitud coseno sobre PostgreSQL con extensión pgvector.

#### `similarity_search(query_vector, k, min_similarity)`

Ejecuta SQL nativo (no ORM) para aprovechar el operador `<=>` de pgvector:

```sql
SELECT ...,
    1 - (embedding <=> '[vector]'::vector) AS similarity
FROM image_embeddings
WHERE 1 - (embedding <=> '[vector]'::vector) >= :min_sim
ORDER BY embedding <=> '[vector]'::vector
LIMIT :k
```

- `<=>` calcula distancia coseno (0 = idénticos, 2 = opuestos).
- `1 - distancia` convierte a similitud (1 = idénticos).
- El índice `ivfflat` acelera el ORDER BY usando búsqueda aproximada de vecinos.

Retorna lista de dicts con `site_name`, `taxonomy`, `similarity_score` (redondeado a 4 decimales) y metadatos del motivo.

#### `upsert(records)`

Inserta embeddings de referencia al corpus. Se usa desde `scripts/seed_embeddings.py` para cargar el dataset inicial. No hace upsert real (no hay ON CONFLICT); inserta nuevos registros.

---

## 6. Orquestador — pipeline principal

**Archivo:** `orchestrator/comparator.py`

Coordina el flujo completo desde una imagen hasta las aristas persistidas.

### 6.1 `compare_image(image_path, site, municipality, department, session, graph)`

Función principal asíncrona. Ejecuta 4 pasos en secuencia:

#### Paso 1 — Extracción de embedding

```
imagen_path → extract_image_embedding() → vector[1280] | None
```

Si el embedding es `None` (modelo no cargado o archivo no encontrado), retorna inmediatamente con `embedding_available: False`.

#### Paso 2 — Búsqueda vectorial

```
vector[1280] → ImageVectorAdapter.similarity_search(k=5, min_sim=0.60)
               → lista de matches {site_name, taxonomy, similarity_score, ...}
```

Busca los `IMAGE_TOP_K` (default 5) motivos más similares en el corpus con similitud ≥ `IMAGE_MIN_SIMILARITY` (default 0.60).

#### Paso 3 — Actualización del grafo en memoria

Para cada match con `similarity_score >= EDGE_MIN_SIMILARITY` (0.70):

```python
graph.add_or_update_edge(site_a=site, site_b=match["site_name"],
                         weight=score, taxonomy=taxonomy)
```

El grafo en memoria se actualiza inmediatamente, antes de tocar la BD. Esto permite que el grafo refleje el estado actual sin esperar el commit.

#### Paso 4 — Persistencia en BD

Llama a `_persist_edges()` con la sesión abierta.

#### Respuesta

```json
{
  "matches": [...],        // todos los matches del corpus (>= 0.60)
  "graph_updated": true,   // si se añadió al menos 1 arista en memoria
  "edges_persisted": 2,    // aristas escritas/actualizadas en BD
  "latency_ms": 145,
  "embedding_available": true
}
```

### 6.2 `_get_or_create_site(session, name, municipality)`

Busca un sitio por nombre; si no existe, lo crea. Maneja race conditions con try/except en `IntegrityError`: si dos requests crean el mismo sitio simultáneamente, el segundo hace retry y lee el que ya existe.

Retorna el UUID del sitio o `None` si falla.

### 6.3 `_persist_edges(session, current_site_name, current_municipality, matches)`

Para cada match con `score >= EDGE_MIN_SIMILARITY`:

1. Obtiene/crea el UUID del sitio origen y del sitio destino.
2. Ordena los dos UUIDs lexicográficamente para la constraint única.
3. Busca la arista existente en BD.

**Si existe (UPDATE):**
```python
n = existing.evidence_count
existing.weight = round((existing.weight * n + score) / (n + 1), 4)
existing.evidence_count = n + 1
# acumula taxonomías únicas
existing.is_provisional = not (
    existing.weight >= settings.edge_reliable_min_similarity
    and existing.evidence_count >= settings.edge_min_evidence
)
```
El peso se actualiza con **promedio ponderado acumulativo**: las observaciones antiguas pesan proporcionalmente más cuantas más hay.

**Si no existe (INSERT):**
```python
SiteGraphEdge(weight=score, evidence_count=1, is_provisional=True)
```
Toda arista nueva comienza como provisional (una sola evidencia).

Usa `session.flush()` al final para escribir cambios sin hacer commit (el commit lo gestiona `get_session`).

---

## 7. Grafo social en memoria

**Archivo:** `graphs/social_graph.py`

Clase `PetroglyphSocialGraph` que encapsula un grafo no dirigido ponderado de NetworkX (`nx.Graph`).

Nodos = nombres de sitios (strings). Aristas = similitud entre pares de sitios.

### 7.1 Construcción

#### `add_site(site_id, *, municipality, department, dominant_taxonomy, petroglyph_count, latitude, longitude)`

Añade o reemplaza un nodo con sus atributos. El `site_id` es el nombre del sitio (no el UUID de BD) para mayor legibilidad en visualizaciones.

#### `add_or_update_edge(site_a, site_b, weight, taxonomy="")`

1. Crea los nodos si no existen.
2. Si la arista ya existe: promedio ponderado acumulativo del peso + incremento de `evidence_count` + acumulación de taxonomías únicas.
3. Si es nueva: crea con `weight`, `evidence_count=1`, `shared_taxonomies`.
4. Siempre al final, recalcula `is_provisional`:
```python
is_provisional = not (
    weight >= settings.edge_reliable_min_similarity
    and evidence_count >= settings.edge_min_evidence
)
```

### 7.2 Subgrafo confiable

#### `_reliable_subgraph() → nx.Graph`

Retorna una **vista inmutable** del grafo original (no copia) que solo incluye aristas con `is_provisional=False`. Se construye con `nx.Graph.edge_subgraph()` que crea una vista lazy — no duplica datos.

```python
reliable = [(u, v) for u, v, d in self._G.edges(data=True)
            if not d.get("is_provisional", True)]
return self._G.edge_subgraph(reliable)
```

Esta vista se usa en los tres algoritmos analíticos para garantizar estabilidad.

### 7.3 Algoritmos de análisis

Todos operan sobre `_reliable_subgraph()` (no el grafo completo) para evitar que aristas con una sola evidencia sesguen los resultados.

#### `pagerank(alpha=0.85) → dict[site, score]`

PageRank estándar de NetworkX ponderado por `weight`. Un sitio con alta similitud a muchos otros con alta similitud recibe PageRank alto. Identifica los sitios más "centrales" en la red iconográfica.

El parámetro `alpha=0.85` es el factor de amortiguamiento clásico (85% de seguir aristas, 15% de salto aleatorio).

#### `communities() → list[set[str]]`

Detección de comunidades con algoritmo **Louvain** (`python-louvain`). Si no está instalado, usa **greedy modularity** de NetworkX como fallback. Ambos maximizan la modularidad del grafo: grupos donde los sitios internos tienen más conexiones entre sí que hacia afuera.

Retorna lista de sets de nombres de sitios. Cada set = un grupo iconográfico regional.

#### `betweenness_centrality() → dict[site, score]`

Centralidad de intermediación normalizada. Mide cuántos caminos más cortos entre pares de sitios pasan por cada nodo. Identifica sitios que actúan como puentes entre regiones estilísticas distintas.

#### `most_similar_sites(site_id, top_k=5) → list[dict]`

Top-k vecinos directos ordenados por peso. Opera sobre `self._G` completo (no solo confiables) para que el usuario vea todas las conexiones, incluyendo las provisionales. Cada resultado incluye `is_provisional` y `confidence_level`.

#### `metrics() → dict`

Métricas topológicas del grafo **completo** (todas las aristas, incluidas provisionales):

| Métrica | Descripción |
|---|---|
| `nodes` / `edges` | Conteos totales |
| `density` | `2E / (N * (N-1))` — fracción de aristas posibles que existen |
| `avg_similarity` | Media de todos los pesos |
| `clustering_coefficient` | Transitividad media ponderada |
| `connected_components` | Número de subgrafos desconectados |
| `largest_component_size` | Nodos en el componente mayor |
| `diameter` | Distancia máxima en el componente mayor |
| `degree_distribution.avg_degree` | Grado promedio |
| `degree_distribution.top_hubs` | 5 sitios con más conexiones |

### 7.4 Serialización y exportación

#### `to_dict() → dict`

Serializa el grafo a JSON para la API. Cada arista incluye todos sus atributos más `confidence_level` calculado al vuelo. Incluye `summary()` con las métricas básicas y `generated_at`.

#### `save_json(path)`

Guarda `to_dict()` en `storage/graphs/social_graph.json`.

#### `export_html(path, height="750px") → str`

Exporta visualización interactiva con **PyVis** (fondo oscuro, física de partículas Force Atlas 2):
- Nodos: tamaño proporcional a PageRank × 500, color por comunidad Louvain.
- Aristas: grosor = `weight * 5`, tooltip con similitud, evidencias y taxonomías.
- 8 colores predefinidos para hasta 8 comunidades.

#### `export_plotly(path) → str`

Exporta con **Plotly** (fondo blanco, más legible para presentaciones):
- Layout con `nx.spring_layout` semilla fija (reproducible).
- Un trace por comunidad (aparecen en la leyenda).
- Aristas como trazos con opacidad 50%, etiquetas de porcentaje de similitud en el punto medio.
- Nodos: tamaño = `20 + PageRank × 600`.

#### `sync_to_db(session)`

Sincroniza todo el grafo en memoria a `site_graph_edges`. Hace upsert manual: si la arista ya existe, actualiza `weight`, `evidence_count`, `shared_taxonomies` e `is_provisional`; si no, inserta.

### 7.5 `_compute_confidence_level(weight, evidence_count) → str`

Función de módulo (no método). Clasifica una arista en tres niveles:

| Nivel | Condición |
|---|---|
| `"high"` | `weight >= 0.85` AND `evidence_count >= 3` |
| `"medium"` | `weight >= 0.76` AND `evidence_count >= 2` (no cumple "high") |
| `"low"` | cualquier otro caso (arista provisional) |

---

## 8. API REST

**Archivo:** `adapters/inbound/api/main.py`

FastAPI con CORS abierto (`*`). Un grafo en memoria `_graph` se comparte entre todos los requests para evitar reconstruirlo en cada llamada de escritura.

### Arranque

`@app.on_event("startup")` llama a `_build_graph_from_db()` y carga todas las aristas y nodos desde la BD en `_graph`. Los endpoints de analytics reconstruyen el grafo desde BD cada vez (son de solo lectura y necesitan datos frescos).

### Helper `_build_graph_from_db(session) → PetroglyphSocialGraph`

1. Carga todos los `RupestranSiteModel` y los añade como nodos.
2. Carga todos los `SiteGraphEdge` y llama `add_or_update_edge()` por cada uno.
3. `add_or_update_edge()` recalcula `is_provisional` al vuelo desde settings.

### Endpoints

#### `GET /health`
Ejecuta `SELECT 1` contra la BD. Retorna `status: "ok"` o `"degraded"` con detalle del error.

#### `POST /compare`
Cuerpo: `{image_path, site, municipality, department}`.

Llama al orquestador. Actualiza `_graph` en memoria y persiste en BD. Retorna matches + estadísticas del pipeline.

#### `GET /sites`
Lista sitios con filtros opcionales `?department=Boyacá&municipality=Gámeza` (case-insensitive, LIKE). Retorna `SiteResponse[]`.

#### `GET /sites/{site_id}`
Detalle completo de un sitio + sus `iconographic_connections`:
```json
{
  "connected_site_id": "uuid",
  "weight": 0.8312,
  "evidence_count": 4,
  "shared_taxonomies": ["Geométrico"],
  "is_provisional": false,
  "confidence_level": "medium"
}
```

#### `GET /graph`
JSON completo del grafo con todos los nodos, aristas (incluidas provisionales) y summary. Cada arista incluye `confidence_level`.

#### `GET /graph/export`
Descarga HTML con PyVis. Reconstruye el grafo completo desde BD, lo exporta y sirve con `FileResponse`.

#### `GET /graph/export/plotly`
Descarga HTML con Plotly. Mismo flujo que el anterior pero con exportación Plotly.

#### `GET /graph/pagerank`
Reconstruye el grafo desde BD, llama a `graph.pagerank()` (solo aristas confiables). Retorna sitios ordenados por score descendente + `top_site`.

#### `GET /graph/communities`
Retorna lista de comunidades (arrays de nombres de sitios) detectadas sobre aristas confiables.

#### `GET /graph/betweenness`
Centralidad de intermediación sobre aristas confiables. Retorna sitios ordenados + `top_bridge_site`.

#### `GET /graph/metrics`
Métricas topológicas del grafo completo (todas las aristas).

#### `GET /graph/sites/{site_id}/similar?top_k=5`
Top-k vecinos por UUID. Busca el nombre del sitio, llama a `most_similar_sites()`. Retorna vecinos con `confidence_level` e `is_provisional`.

---

## 9. Scripts de operaciones

### `scripts/migrate.py`

Lee `infrastructure/database/migrations/schema.sql` completo y lo ejecuta contra Supabase usando psycopg2 (síncrono). Se corre una sola vez para crear las tablas.

```bash
python scripts/migrate.py
```

### `scripts/migrate_add_provisional.py`

Migración incremental para instancias ya desplegadas. Hace dos operaciones:

1. `ALTER TABLE site_graph_edges ADD COLUMN IF NOT EXISTS is_provisional BOOLEAN NOT NULL DEFAULT TRUE` — añade la columna si no existe.
2. `UPDATE site_graph_edges SET is_provisional = NOT (weight >= X AND evidence_count >= Y)` — reclasifica todas las aristas existentes según los umbrales actuales de settings.

Al final reporta cuántas aristas quedaron como confiables vs provisionales.

```bash
python scripts/migrate_add_provisional.py
```

### `scripts/seed_embeddings.py`

Carga el corpus de referencia inicial. Lee imágenes de un directorio o CSV, extrae embeddings con EfficientNet-B0 y los inserta en `image_embeddings` vía `ImageVectorAdapter.upsert()`.

### `scripts/bulk_compare.py`

Compara todo un corpus de imágenes contra el índice vectorial y construye el grafo social completo. Útil para el primer llenado o para recomputar todo el grafo.

Recibe un CSV con columnas `image_path`, `site_name`, `municipality`, `department`.

Características clave:
- Una sesión DB independiente por imagen: un error en una imagen no afecta las demás.
- Reporta progreso cada 10 imágenes.
- Al final imprime resumen del grafo resultante.

```bash
python -m scripts.bulk_compare --csv storage/reference_images/manifest.csv
```

---

## 10. Sistema de confianza de aristas

El sistema implementa un **doble criterio** para distinguir aristas con evidencia sólida de conexiones provisionales basadas en una sola observación.

### Criterio de confiabilidad

Una arista es **confiable** (`is_provisional = False`) cuando cumple ambas condiciones simultáneamente:

```
weight >= EDGE_RELIABLE_MIN_SIMILARITY (0.76)  ← similitud promedio alta
AND
evidence_count >= EDGE_MIN_EVIDENCE (2)         ← confirmado en ≥ 2 comparaciones
```

Si falla cualquiera de las dos, la arista es **provisional** (`is_provisional = True`).

### Ciclo de vida de una arista

```
Primera comparación:
  score=0.78 → INSERT con evidence_count=1, is_provisional=True
               (una evidencia no es suficiente)

Segunda comparación:
  score=0.81 → UPDATE weight=0.795 (promedio), evidence_count=2
               0.795 >= 0.76 AND 2 >= 2 → is_provisional=False ✓

Tercera comparación:
  score=0.74 → UPDATE weight=0.780 (promedio), evidence_count=3
               0.780 >= 0.76 AND 3 >= 2 → is_provisional=False ✓
               confidence_level: "medium"

Si el promedio sube a 0.85+ con 3+ evidencias:
               confidence_level: "high"
```

### Impacto en algoritmos

| Algoritmo | Usa aristas provisionales |
|---|---|
| `pagerank()` | No — solo confiables |
| `communities()` | No — solo confiables |
| `betweenness_centrality()` | No — solo confiables |
| `metrics()` | Sí — todos los nodos/aristas |
| `most_similar_sites()` | Sí — pero las marca con `is_provisional` |
| `to_dict()` / `/graph` | Sí — muestra todo con `confidence_level` |

### Calibración del umbral

El umbral `EDGE_RELIABLE_MIN_SIMILARITY` se puede ajustar en `.env`:

```
EDGE_RELIABLE_MIN_SIMILARITY=0.74   # más permisivo: más aristas confiables
EDGE_RELIABLE_MIN_SIMILARITY=0.78   # más estricto: solo similitudes altas
```

Para calibrar, ejecutar `scripts/migrate_add_provisional.py` tras cada cambio — reporta la distribución resultante sin tocar ninguna arista del pipeline activo.

---

## 11. Flujo de datos completo

```
┌──────────────────────────────────────────────────────────────────────────┐
│  INPUT: POST /compare                                                    │
│  { image_path: "storage/images/gameza_espiral.jpg",                      │
│    site: "Gámeza", municipality: "Gámeza", department: "Boyacá" }        │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                     ┌──────────▼──────────┐
                     │  EfficientNet-B0    │
                     │  Resize(224,224)    │
                     │  Normalize(ImageNet)│
                     │  → vector[1280]     │
                     └──────────┬──────────┘
                                │
                     ┌──────────▼──────────────────────────────────┐
                     │  pgvector similarity_search                 │
                     │  SELECT ... 1-(embedding <=> query)         │
                     │  WHERE similarity >= 0.60                   │
                     │  ORDER BY distance LIMIT 5                  │
                     │  → [{site_name:"Villa de Leyva",            │
                     │       taxonomy:"Geométrico",                │
                     │       similarity_score:0.83}, ...]          │
                     └──────────┬──────────────────────────────────┘
                                │
               ┌────────────────┴─────────────────────┐
               │  Para cada match con score >= 0.70   │
               └────────────────┬─────────────────────┘
                                │
          ┌─────────────────────┴──────────────────────────┐
          │                                                │
 ┌────────▼────────────────────┐          ┌───────────────▼───────────────┐
 │  Grafo en memoria (NetworkX)│          │  PostgreSQL (Supabase)        │
 │  add_or_update_edge(        │          │  _persist_edges()             │
 │    "Gámeza",                │          │  UPSERT site_graph_edges      │
 │    "Villa de Leyva",        │          │  weight = avg ponderado       │
 │    weight=0.83,             │          │  evidence_count++             │
 │    taxonomy="Geométrico"    │          │  is_provisional = NOT (       │
 │  )                          │          │    weight>=0.76               │
 │  → recalcula is_provisional │          │    AND evidence_count>=2      │
 └─────────────────────────────┘          │  )                            │
                                          └───────────────────────────────┘
                                                         │
                                          ┌──────────────▼──────────────┐
                                          │  OUTPUT: CompareResponse     │
                                          │  { matches: [...],           │
                                          │    graph_updated: true,      │
                                          │    edges_persisted: 1,       │
                                          │    latency_ms: 132 }         │
                                          └─────────────────────────────┘
```

### Flujo de lectura analítica

```
GET /graph/pagerank
        │
        ▼
_build_graph_from_db()
  ↳ SELECT * FROM rupestrian_sites  → add_site() por cada uno
  ↳ SELECT * FROM site_graph_edges  → add_or_update_edge() por cada una
                                       (recalcula is_provisional)
        │
        ▼
graph.pagerank()
  ↳ _reliable_subgraph()
      ↳ filtra aristas donde is_provisional=False
  ↳ nx.pagerank(subgraph, alpha=0.85, weight="weight")
        │
        ▼
{ "pagerank": {"Gámeza": 0.152, "Villa de Leyva": 0.143, ...},
  "top_site": "Gámeza" }
```

---

## 12. Esquema de la base de datos

```
rupestrian_sites                    image_embeddings
─────────────────────               ──────────────────────
id          UUID PK                 id            UUID PK
name        TEXT UNIQUE             petroglyph_id TEXT
municipality TEXT                   site_name     TEXT
department  TEXT                    municipality  TEXT
latitude    FLOAT                   reference_name TEXT
longitude   FLOAT                   taxonomy      TEXT
conservation_status TEXT            image_path    TEXT
dominant_taxonomy   TEXT            embedding     VECTOR(1280)
petroglyph_count    INT             metadata      JSONB
metadata    JSONB                   created_at    TIMESTAMPTZ
created_at  TIMESTAMPTZ             [INDEX ivfflat cosine]
     │
     │ FK site_a_id
     │ FK site_b_id
     ▼
site_graph_edges
────────────────────────────────
id                UUID PK
site_a_id         UUID FK → rupestrian_sites
site_b_id         UUID FK → rupestrian_sites
weight            FLOAT          ← similitud coseno promedio
shared_taxonomies JSONB[]        ← taxonomías de motivos comunes
evidence_count    INTEGER        ← número de confirmaciones
is_provisional    BOOLEAN        ← no cumple doble criterio
created_at        TIMESTAMPTZ
updated_at        TIMESTAMPTZ    ← actualizado por trigger
[UNIQUE (site_a_id, site_b_id)]
```

---

*Documento generado para el proyecto UPTC 2026 — Grafos Sociales Rupestres.*
