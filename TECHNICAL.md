# Módulo de Grafos Sociales — Documentación Técnica

## Índice

1. [Visión general](#1-visión-general)
2. [Arquitectura](#2-arquitectura)
3. [Flujo de datos end-to-end](#3-flujo-de-datos-end-to-end)
4. [Componentes](#4-componentes)
   - 4.1 [Configuración](#41-configsettingspy)
   - 4.2 [Base de datos](#42-infrastructure--database)
   - 4.3 [Extractor de embeddings](#43-adaptersoutboundembeddingsefficientnet_adapterpy)
   - 4.4 [Adaptador pgvector](#44-adaptersoutboundvector_storepgvector_adapterpy)
   - 4.5 [Grafo social](#45-graphssocial_graphpy)
   - 4.6 [Pipeline comparador](#46-orchestratorcomparatorpy)
   - 4.7 [API REST](#47-adaptersinboundapimainpy)
5. [Esquema de base de datos](#5-esquema-de-base-de-datos)
6. [Algoritmos del grafo](#6-algoritmos-del-grafo)
7. [Scripts de utilidad](#7-scripts-de-utilidad)
8. [Stack tecnológico](#8-stack-tecnológico)

---

## 1. Visión general

El módulo de grafos sociales construye una **red de similitud iconográfica entre sitios rupestres** de la región andina colombiana. La premisa central es que dos sitios arqueológicos están relacionados si los motivos grabados en sus petroglifos son visualmente similares, independientemente de su distancia geográfica.

El sistema responde preguntas como:
- ¿Qué sitios comparten la misma tradición iconográfica?
- ¿Cuáles son los sitios más "centrales" en la red rupestre?
- ¿Existen comunidades de sitios con estilos afines?
- ¿Hay sitios que actúan como puentes entre distintas tradiciones?

La similitud **no es subjetiva ni hardcodeada**: se calcula sobre embeddings reales extraídos por una red neuronal (EfficientNet-B0) y almacenados en una base de datos vectorial (pgvector sobre PostgreSQL/Supabase).

---

## 2. Arquitectura

El módulo sigue la **arquitectura hexagonal** del proyecto original, separando el dominio del negocio de los detalles de infraestructura:

```
grafos_sociales/
├── adapters/
│   ├── inbound/api/main.py          ← Entrada: HTTP (FastAPI)
│   └── outbound/
│       ├── embeddings/              ← Salida: EfficientNet-B0 (PyTorch)
│       └── vector_store/            ← Salida: PostgreSQL + pgvector
├── core/domain/                     ← Entidades y enumeraciones del dominio
├── graphs/social_graph.py           ← Lógica central del grafo (NetworkX)
├── infrastructure/database/         ← ORM, sesión, migraciones
├── orchestrator/comparator.py       ← Coordinación del pipeline
├── config/settings.py               ← Configuración centralizada
└── scripts/                         ← Utilidades CLI
```

**Principio clave:** la lógica del grafo (`graphs/`) no conoce nada de HTTP ni de PostgreSQL. Los adaptadores traducen entre el mundo exterior y el dominio.

---

## 3. Flujo de datos end-to-end

```
[Usuario] POST /compare {image_path, site, municipality}
              │
              ▼
     orchestrator/comparator.py
     └── compare_image()
              │
              ├─ 1. extract_embedding(image_path)
              │       EfficientNet-B0 → vector de 1280 dimensiones
              │
              ├─ 2. ImageVectorAdapter.similarity_search(embedding)
              │       SELECT ... FROM image_embeddings
              │       WHERE cosine_sim >= 0.60
              │       ORDER BY distancia coseno
              │       → lista de {site_name, taxonomy, similarity_score}
              │
              ├─ 3. graph.add_or_update_edge(site_a, site_b, weight)
              │       Actualiza el grafo NetworkX en memoria
              │       Solo si similarity_score >= 0.70
              │
              └─ 4. _persist_edges(session, ...)
                      INSERT/UPDATE en site_graph_edges (PostgreSQL)
                      Promedio acumulativo del peso si ya existe la arista

[Usuario] GET /graph
              │
              ▼
     _build_graph_from_db(session)
     └── SELECT * FROM rupestrian_sites
     └── SELECT * FROM site_graph_edges
     └── Reconstruye PetroglyphSocialGraph desde BD
              │
              ▼
     graph.to_dict() → JSON con nodos, aristas y métricas

[Usuario] GET /graph/export/plotly
              │
              ▼
     graph.export_plotly() → HTML interactivo con Plotly
```

---

## 4. Componentes

### 4.1 `config/settings.py`

Gestiona toda la configuración mediante **pydantic-settings**, que lee variables de entorno desde `.env`. Centralizar la configuración aquí garantiza que ningún componente tenga credenciales o parámetros hardcodeados.

**Variables relevantes:**

| Variable | Propósito | Valor por defecto |
|---|---|---|
| `DATABASE_URL` | URL async (asyncpg) para SQLAlchemy | — |
| `DATABASE_URL_SYNC` | URL sync (psycopg2) para scripts CLI | — |
| `image_top_k` | Máximo de resultados en búsqueda pgvector | 5 |
| `image_min_similarity` | Umbral mínimo para retornar un match | 0.60 |
| `edge_min_similarity` | Umbral para crear arista en el grafo | 0.70 |

**Detalle técnico — fix para Supabase:**

El host directo de Supabase (`db.*.supabase.co:5432`) resuelve exclusivamente a IPv6. El pooler de Supabase (`*.pooler.supabase.com:6543`) tiene registros IPv4. En redes sin soporte IPv6, `settings.py` deriva automáticamente la URL async desde `DATABASE_URL_SYNC` (que apunta al pooler), reemplazando el driver `psycopg2` por `asyncpg`:

```python
@model_validator(mode="after")
def _fix_db_driver(self) -> "Settings":
    self.database_url = self.database_url_sync.replace("+psycopg2", "+asyncpg", 1)
    return self
```

El pooler de Supabase usa **PgBouncer en modo transacción**, que no soporta prepared statements. Por eso el engine de SQLAlchemy se crea con `connect_args={"statement_cache_size": 0}`, que desactiva el caché de statements en asyncpg.

---

### 4.2 `infrastructure / database`

#### `session.py`

Define el engine async de SQLAlchemy y la función generadora `get_session()` que se usa como dependencia en FastAPI:

```python
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,        # verifica conexión antes de usarla
    pool_size=5,
    connect_args={"statement_cache_size": 0},  # PgBouncer compatibility
)
```

`get_session()` es un generador async que hace commit automático al final del request y rollback si ocurre una excepción. FastAPI lo inyecta con `Depends(get_session)`.

#### `models/models.py`

Define tres modelos ORM con SQLAlchemy 2.0 (mapped columns):

**`RupestranSiteModel`** — Nodos del grafo
```
rupestrian_sites
├── id (UUID PK)
├── name (UNIQUE)
├── municipality, department
├── latitude, longitude
├── dominant_taxonomy
└── petroglyph_count
```

**`ImageEmbedding`** — Corpus de referencia para pgvector
```
image_embeddings
├── id (UUID PK)
├── site_name, municipality
├── taxonomy
├── image_path
├── embedding (VECTOR(1280))   ← dimensión de EfficientNet-B0
└── metadata (JSONB)
```
Tiene un índice **IVFFlat** sobre `embedding` con operador `vector_cosine_ops` para búsqueda aproximada eficiente por similitud coseno.

**`SiteGraphEdge`** — Aristas del grafo
```
site_graph_edges
├── id (UUID PK)
├── site_a_id (FK → rupestrian_sites)
├── site_b_id (FK → rupestrian_sites)
├── weight (FLOAT)             ← similitud coseno promedio
├── shared_taxonomies (JSONB)  ← taxonomías que comparten
├── evidence_count (INT)       ← número de comparaciones que generaron esta arista
└── UNIQUE(site_a_id, site_b_id)
```
La restricción UNIQUE garantiza que cada par de sitios tiene como máximo una arista. Cuando se detecta una similitud repetida, se actualiza el peso con **promedio acumulativo** en lugar de insertar duplicados.

---

### 4.3 `adapters/outbound/embeddings/efficientnet_adapter.py`

Extrae un vector de características de 1280 dimensiones de una imagen usando **EfficientNet-B0** preentrenado en ImageNet (cargado con `timm`).

**Por qué EfficientNet-B0:**
- Balance entre precisión y velocidad (8.6M parámetros vs 25M de ResNet-50)
- Preentrenado en ImageNet → ya aprendió a detectar formas, texturas y patrones visuales
- Se usa como extractor de características (`num_classes=0`) sin cabeza de clasificación

**Pipeline de extracción:**

```python
_TRANSFORM = T.Compose([
    T.Resize((224, 224)),          # EfficientNet espera 224×224
    T.ToTensor(),
    T.Normalize(                   # normalización ImageNet
        mean=[0.485, 0.456, 0.406],
        std=[0.229, 0.224, 0.225]
    ),
])

def extract_image_embedding(image_path: str) -> list[float]:
    img = Image.open(image_path).convert("RGB")
    tensor = _TRANSFORM(img).unsqueeze(0)   # batch de 1
    with torch.no_grad():
        features = _MODEL(tensor)           # shape: [1, 1280]
    return features.squeeze().numpy().tolist()
```

El modelo se carga una sola vez al inicio (`_MODEL = _load_efficientnet()`) y se reutiliza en todas las llamadas, evitando overhead de carga repetida.

**Qué captura el embedding:**
El vector de 1280 dimensiones codifica características visuales abstractas: formas, texturas, distribución espacial de elementos. Dos imágenes con motivos visualmente similares producirán vectores con alto producto punto (alta similitud coseno), independientemente del tamaño, orientación o color de la imagen.

---

### 4.4 `adapters/outbound/vector_store/pgvector_adapter.py`

`ImageVectorAdapter` encapsula la búsqueda de similitud coseno sobre la tabla `image_embeddings` usando la extensión **pgvector** de PostgreSQL.

**Consulta SQL central:**

```sql
SELECT
    site_name, municipality, taxonomy, image_path,
    1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity
FROM image_embeddings
WHERE 1 - (embedding <=> CAST(:query_vec AS vector)) >= :min_sim
ORDER BY embedding <=> CAST(:query_vec AS vector)
LIMIT :k
```

El operador `<=>` es la **distancia coseno** definida por pgvector: `distancia = 1 - similitud`. Se usa `CAST(... AS vector)` en vez de `::vector` para evitar conflictos del parser de SQLAlchemy con asyncpg al interpretar los dos puntos.

**Por qué similitud coseno:**
La similitud coseno mide el ángulo entre dos vectores, ignorando su magnitud. Esto es adecuado para embeddings de imágenes porque dos petroglifos del mismo estilo producen vectores que apuntan en la misma dirección en el espacio de 1280 dimensiones, aunque sus intensidades absolutas difieran.

**Índice IVFFlat:**
Con más de 100 registros, el script `seed_embeddings.py` crea un índice IVFFlat (Inverted File with Flat quantization). Este índice divide el espacio vectorial en `lists=50` clusters (celdas de Voronoi) y en cada búsqueda solo explora las celdas más cercanas. Esto reduce la complejidad de `O(n)` a aproximadamente `O(√n)` con una pequeña pérdida de precisión (búsqueda aproximada de vecinos más cercanos).

---

### 4.5 `graphs/social_graph.py`

Clase central `PetroglyphSocialGraph` que envuelve un grafo **no dirigido ponderado** de NetworkX (`nx.Graph`).

**Estructura del grafo:**
- **Nodos**: sitios arqueológicos (identificados por nombre)
- **Atributos de nodo**: municipio, departamento, taxonomía dominante, conteo de petroglifos, coordenadas
- **Aristas**: similitud iconográfica entre pares de sitios
- **Atributos de arista**: `weight` (similitud 0–1), `evidence_count` (número de comparaciones), `shared_taxonomies`

**Actualización de aristas con promedio acumulativo:**

Cuando se detecta una similitud entre dos sitios que ya tienen arista, el peso se actualiza con promedio acumulativo para evitar sesgos hacia comparaciones tempranas:

```python
n = data.get("evidence_count", 1)
new_weight = (data["weight"] * n + weight) / (n + 1)
data["weight"] = round(new_weight, 4)
data["evidence_count"] = n + 1
```

**Persistencia en PostgreSQL (`sync_to_db`):**

Sincroniza todas las aristas del grafo en memoria a `site_graph_edges`. Resuelve los UUIDs de los sitios por nombre consultando `rupestrian_sites`, luego hace upsert manual (SELECT → INSERT/UPDATE) porque SQLAlchemy ORM no tiene `INSERT ... ON CONFLICT DO UPDATE` nativo en modo async.

**Reconstrucción desde BD (`_build_graph_from_db` en la API):**

Cada llamada a `GET /graph` reconstruye el grafo desde cero leyendo `rupestrian_sites` y `site_graph_edges`. Usa el **nombre del sitio** (no el UUID) como identificador de nodo para que los edges cargados desde BD conecten correctamente con los nodos.

**Visualización con Plotly (`export_plotly`):**

1. Calcula posiciones con `nx.spring_layout` (algoritmo force-directed, semilla fija para reproducibilidad)
2. Asigna colores por comunidad Louvain
3. Escala el tamaño de nodos según PageRank
4. Construye trazas de Plotly: una por arista (líneas), una por comunidad (nodos)
5. Añade etiquetas de similitud en el punto medio de cada arista
6. Exporta HTML self-contained con Plotly.js via CDN

---

### 4.6 `orchestrator/comparator.py`

Función `compare_image()` que coordina el pipeline completo. Reemplaza al agente A3 del sistema original eliminando la abstracción de LangGraph: es una función async pura sin estado.

```python
async def compare_image(
    image_path: str,
    site: str,
    municipality: str,
    department: str,
    session: AsyncSession,
    graph: PetroglyphSocialGraph,
) -> dict:
```

**Pasos internos:**

1. **Embedding**: llama `extract_image_embedding(image_path)` → vector 1280-dim
2. **Búsqueda pgvector**: crea `ImageVectorAdapter(session)` y busca los k más similares con similitud ≥ `image_min_similarity` (0.60)
3. **Actualización en memoria**: para matches con score ≥ `edge_min_similarity` (0.70) actualiza el grafo NetworkX
4. **Persistencia**: llama `_persist_edges()` que:
   - Resuelve o crea el UUID del sitio actual en `rupestrian_sites` (`_get_or_create_site`)
   - Para cada match válido, resuelve o crea el UUID del sitio match
   - Normaliza el orden de los UUIDs (`sorted([id_a, id_b])`) para cumplir la restricción UNIQUE
   - Si la arista existe: actualiza peso con promedio acumulativo
   - Si no existe: INSERT nuevo registro
   - `flush()` sin commit (el commit lo hace el caller)

**Manejo de race conditions:**

`_get_or_create_site` captura `IntegrityError` (que ocurre si dos workers intentan crear el mismo sitio simultáneamente) y relee el registro existente:

```python
try:
    session.add(new_site)
    await session.flush()
    return new_site.id
except IntegrityError:
    await session.rollback()
    # Releer el registro creado por el otro worker
    result = await session.execute(select(...).where(name == name))
    return result.scalar_one_or_none().id
```

---

### 4.7 `adapters/inbound/api/main.py`

API REST construida con **FastAPI**. Todos los endpoints que leen el grafo reconstruyen el `PetroglyphSocialGraph` desde BD en cada request (stateless), garantizando consistencia sin gestionar estado en memoria entre requests.

**Endpoints:**

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/compare` | Pipeline completo: embed → buscar → actualizar grafo → persistir |
| `GET` | `/sites` | Lista sitios con filtro opcional por departamento/municipio |
| `GET` | `/sites/{site_id}` | Detalle de un sitio y sus conexiones iconográficas |
| `GET` | `/graph` | Grafo completo como JSON (nodos + aristas + métricas) |
| `GET` | `/graph/export` | Visualización PyVis (HTML, fondo oscuro, física de partículas) |
| `GET` | `/graph/export/plotly` | Visualización Plotly (HTML, fondo blanco, etiquetas claras) |
| `GET` | `/graph/pagerank` | Ranking PageRank de todos los sitios |
| `GET` | `/graph/communities` | Comunidades Louvain detectadas |
| `GET` | `/graph/betweenness` | Centralidad de intermediación por sitio |
| `GET` | `/graph/metrics` | Métricas topológicas del grafo |
| `GET` | `/graph/sites/{site_id}/similar` | Top-k sitios más similares a uno dado |
| `GET` | `/health` | Estado de la API y la BD |

---

## 5. Esquema de base de datos

```sql
-- Nodos del grafo
CREATE TABLE rupestrian_sites (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL UNIQUE,
    municipality        TEXT DEFAULT '',
    department          TEXT DEFAULT '',
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    dominant_taxonomy   TEXT DEFAULT 'Indeterminado',
    petroglyph_count    INTEGER DEFAULT 0
);

-- Corpus de referencia (embeddings EfficientNet-B0)
CREATE TABLE image_embeddings (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_name      TEXT DEFAULT '',
    municipality   TEXT DEFAULT '',
    taxonomy       TEXT DEFAULT 'Indeterminado',
    image_path     TEXT DEFAULT '',
    embedding      VECTOR(1280),       -- 1280 dimensiones EfficientNet-B0
    metadata       JSONB DEFAULT '{}'
);
-- Índice IVFFlat para búsqueda aproximada por similitud coseno
CREATE INDEX ix_img_embeddings_embedding
    ON image_embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);

-- Aristas del grafo social
CREATE TABLE site_graph_edges (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_a_id         UUID NOT NULL REFERENCES rupestrian_sites(id),
    site_b_id         UUID NOT NULL REFERENCES rupestrian_sites(id),
    weight            FLOAT DEFAULT 0.0,
    shared_taxonomies JSONB DEFAULT '[]',
    evidence_count    INTEGER DEFAULT 1,
    CONSTRAINT uq_site_graph_edge UNIQUE (site_a_id, site_b_id)
);
```

---

## 6. Algoritmos del grafo

### PageRank

Mide la **importancia relativa** de cada nodo propagando "votos" a través de las aristas. Un sitio tiene alto PageRank si está conectado con muchos sitios que a su vez también tienen alto PageRank. Matemáticamente es el vector estacionario de la cadena de Markov definida por el grafo ponderado.

```
PR(u) = (1 - α) / N + α * Σ PR(v) * w(v,u) / Σ w(v,*)
```

Donde α = 0.85 (factor de amortiguación), N = número de nodos. Se calcula con `scipy` (implementación iterativa de potencias).

**Interpretación:** sitio con alto PageRank = hub iconográfico central de la red rupestre.

### Comunidades Louvain

Algoritmo de detección de comunidades basado en **maximización de modularidad**. La modularidad Q mide qué tan denso es el interior de cada comunidad respecto a lo esperado en un grafo aleatorio:

```
Q = Σ_c [ L_c/m - (d_c / 2m)² ]
```

Donde `L_c` = aristas dentro de la comunidad c, `m` = aristas totales, `d_c` = suma de grados en c.

El algoritmo es greedy: comienza con cada nodo en su propia comunidad y fusiona iterativamente las que mayor ganancia de modularidad produzcan. Implementado con `python-louvain`.

**Interpretación:** sitios del mismo color comparten más similitud iconográfica entre sí que con el resto de la red → posible tradición rupestre compartida.

### Betweenness Centrality

Mide cuántos caminos mínimos entre pares de nodos **pasan a través** de un nodo dado:

```
BC(v) = Σ_{s≠v≠t} σ(s,t|v) / σ(s,t)
```

Donde `σ(s,t)` = número de caminos mínimos de s a t, `σ(s,t|v)` = cuántos de esos pasan por v.

**Interpretación:** sitio con alto betweenness = puente entre tradiciones iconográficas distintas. Si se eliminara ese sitio, la red se fragmentaría.

### Coeficiente de clustering

Para cada nodo mide qué fracción de sus vecinos también están conectados entre sí:

```
C(v) = 2 * triangles(v) / (degree(v) * (degree(v) - 1))
```

El valor promedio del grafo indica qué tan "transitiva" es la similitud: si A es similar a B y B es similar a C, ¿también A es similar a C?

---

## 7. Scripts de utilidad

### `scripts/migrate.py`
Crea las tablas en Supabase ejecutando `migrations/schema.sql` via psycopg2 (conexión síncrona, modo `autocommit`). Se usa una sola vez para inicializar la BD.

### `scripts/seed_embeddings.py`
Pobla `image_embeddings` con el corpus de referencia. Acepta dos modos:
- `--folder`: estructura `taxonomy/sitio/imagen.ext` (taxonomía inferida del directorio)
- `--csv`: manifest con columnas `image_path, site_name, municipality, taxonomy, reference_name`

Extrae embeddings EfficientNet-B0 en lotes e inserta en PostgreSQL. Si hay ≥ 100 filas crea el índice IVFFlat automáticamente (requiere datos previos al índice para entrenamiento efectivo).

### `scripts/generate_synthetic_corpus.py`
Genera imágenes sintéticas representando cada taxonomía con formas visuales distintas (PIL):
- **Antropomorfo**: figura humana (cabeza + torso + extremidades) sobre fondo ocre
- **Zoomorfo**: animal ovalado con patas y cola sobre fondo verde
- **Geométrico**: espirales, círculos concéntricos o retícula sobre fondo violeta

Cada imagen tiene ruido de textura aleatorio para producir embeddings con pequeñas variaciones. Genera también el CSV manifest completo con 30 sitios reales de la región andina colombiana (Boyacá, Cundinamarca, Santander, Huila, Nariño, Antioquia).

### `scripts/bulk_compare.py`
Procesa masivamente un manifest CSV llamando a `compare_image()` para cada imagen. Usa una **sesión de BD independiente por imagen** para evitar que un fallo (ej. UniqueViolationError) invalide toda la sesión y detenga el proceso.

---

## 8. Stack tecnológico

| Capa | Tecnología | Versión / Detalle |
|---|---|---|
| **API** | FastAPI + Uvicorn | ASGI, async |
| **ORM** | SQLAlchemy 2.0 | Mapped columns, async session |
| **Driver async** | asyncpg | Compatible con PgBouncer via `statement_cache_size=0` |
| **Base de datos** | PostgreSQL 16 + pgvector | Supabase (cloud) |
| **Búsqueda vectorial** | pgvector | Índice IVFFlat, similitud coseno |
| **Embeddings** | EfficientNet-B0 (timm) | 1280 dims, preentrenado ImageNet |
| **Deep learning** | PyTorch + torchvision | Solo inferencia, sin entrenamiento |
| **Grafo** | NetworkX | Grafo no dirigido ponderado |
| **Comunidades** | python-louvain | Maximización de modularidad |
| **Visualización** | Plotly + PyVis | HTML interactivo exportable |
| **Configuración** | pydantic-settings | `.env` + validación de tipos |
| **Logging** | structlog | JSON estructurado |
| **Imágenes sintéticas** | Pillow (PIL) | Generación procedural |
