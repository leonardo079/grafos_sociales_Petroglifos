# Flujo Técnico Completo — Grafos Sociales Rupestres

Desde la carga del corpus de imágenes hasta la construcción y análisis del grafo social de similitud iconográfica entre sitios rupestres colombianos.

---

## Tabla de contenidos

1. [Visión de alto nivel](#1-visión-de-alto-nivel)
2. [Fase 0 — Infraestructura de base de datos](#2-fase-0--infraestructura-de-base-de-datos)
3. [Fase 1 — Carga del corpus de referencia (seed)](#3-fase-1--carga-del-corpus-de-referencia-seed)
4. [Fase 2 — Extracción de embeddings con EfficientNet-B0](#4-fase-2--extracción-de-embeddings-con-efficientnet-b0)
5. [Fase 3 — Almacenamiento vectorial en pgvector](#5-fase-3--almacenamiento-vectorial-en-pgvector)
6. [Fase 4 — Pipeline de comparación de una imagen nueva](#6-fase-4--pipeline-de-comparación-de-una-imagen-nueva)
7. [Fase 5 — Búsqueda de similitud coseno](#7-fase-5--búsqueda-de-similitud-coseno)
8. [Fase 6 — Actualización del grafo en memoria](#8-fase-6--actualización-del-grafo-en-memoria)
9. [Fase 7 — Persistencia de aristas en PostgreSQL](#9-fase-7--persistencia-de-aristas-en-postgresql)
10. [Fase 8 — Sistema de confianza de aristas](#10-fase-8--sistema-de-confianza-de-aristas)
11. [Fase 9 — Reconstrucción del grafo desde la BD](#11-fase-9--reconstrucción-del-grafo-desde-la-bd)
12. [Fase 10 — Algoritmos de análisis del grafo](#12-fase-10--algoritmos-de-análisis-del-grafo)
13. [Flujo de procesamiento en lote (bulk)](#13-flujo-de-procesamiento-en-lote-bulk)
14. [Diagrama completo de flujo de datos](#14-diagrama-completo-de-flujo-de-datos)
15. [Estructura de datos a lo largo del pipeline](#15-estructura-de-datos-a-lo-largo-del-pipeline)

---

## 1. Visión de alto nivel

El sistema responde a una pregunta concreta: **¿qué tan similares iconográficamente son los petroglifos de dos sitios arqueológicos distintos?**

La respuesta no se construye con reglas escritas a mano sino con **similitud geométrica en espacio vectorial**: si dos imágenes producen vectores cercanos en un espacio de 1280 dimensiones (capturado por una red neuronal entrenada en millones de imágenes), se asume que comparten rasgos visuales significativos.

Esas similitudes se acumulan en el tiempo a través de múltiples comparaciones y cristalizan en un **grafo social ponderado**, donde los nodos son sitios arqueológicos y las aristas representan afinidad iconográfica comprobada con evidencia múltiple.

```
Imágenes de petroglifos
        ↓
  Red neuronal (EfficientNet-B0)
        ↓
  Vectores de 1280 dimensiones
        ↓
  Búsqueda de vecinos más cercanos (pgvector)
        ↓
  Detección de similitudes entre sitios
        ↓
  Acumulación de evidencias a lo largo del tiempo
        ↓
  Grafo social ponderado y filtrado por confianza
        ↓
  PageRank · Comunidades · Betweenness
```

---

## 2. Fase 0 — Infraestructura de base de datos

**Archivo:** `scripts/migrate.py` · `infrastructure/database/migrations/schema.sql`

Antes de cualquier procesamiento, las tres tablas deben existir en Supabase (PostgreSQL con extensión pgvector ya activa).

```bash
python scripts/migrate.py
```

Este script conecta por psycopg2 al pooler de Supabase y ejecuta `schema.sql` íntegro. Las tablas creadas son:

### `rupestrian_sites` — nodos del grafo

```sql
id UUID, name TEXT UNIQUE, municipality TEXT, department TEXT,
latitude FLOAT, longitude FLOAT, conservation_status TEXT,
dominant_taxonomy TEXT, petroglyph_count INTEGER, metadata JSONB
```

Cada fila es un sitio arqueológico. Se auto-crea cuando aparece en una comparación si no existía antes.

### `image_embeddings` — corpus vectorial de referencia

```sql
id UUID, site_name TEXT, taxonomy TEXT, reference_name TEXT,
image_path TEXT, embedding VECTOR(1280), metadata JSONB
```

Cada fila es una imagen de petroglifo ya vectorizada. Esta tabla es el "conocimiento" que el sistema tiene sobre cómo se ven los motivos rupestres de cada sitio.

### `site_graph_edges` — aristas del grafo social

```sql
id UUID,
site_a_id UUID FK, site_b_id UUID FK,
weight FLOAT,           -- similitud coseno promedio acumulada
shared_taxonomies JSONB, -- taxonomías de motivos que aportaron evidencia
evidence_count INTEGER,  -- número de comparaciones que confirmaron la arista
is_provisional BOOLEAN,  -- no cumple el doble criterio de confiabilidad
created_at, updated_at TIMESTAMPTZ
```

Restricción `UNIQUE (site_a_id, site_b_id)`: solo puede existir una arista entre cada par de sitios; se va actualizando con cada nueva evidencia.

Un **trigger** en PostgreSQL actualiza `updated_at` automáticamente en cada `UPDATE`.

---

## 3. Fase 1 — Carga del corpus de referencia (seed)

**Archivo:** `scripts/seed_embeddings.py`

Este es el **punto de entrada del conocimiento** al sistema. Sin corpus, no hay búsquedas posibles.

### ¿Qué es el corpus?

Una colección de imágenes de petroglifos ya clasificadas por taxonomía y sitio de origen. Actúa como base de referencia: cuando llegue una imagen nueva, se buscará qué imágenes del corpus se le parecen más.

### Dos modos de ingestión

#### Modo 1 — Estructura de carpetas

```
storage/reference_images/
├── Geométrico/
│   ├── Piedras_del_Tunjo/
│   │   ├── tunjo_espiral_01.jpg
│   │   └── tunjo_concentric_02.jpg
│   └── Gameza/
│       └── gameza_linea_01.jpg
├── Zoomorfo/
│   └── Villa_de_Leyva/
│       └── leyva_serpiente.jpg
```

El script infiere automáticamente: `taxonomy = nombre de la carpeta padre`, `site_name = nombre de la carpeta intermedia` (guiones bajos → espacios).

#### Modo 2 — CSV manifest

```csv
image_path,site_name,municipality,taxonomy,reference_name
storage/ref/img01.jpg,Piedras del Tunjo,Facatativá,Geométrico,Espiral central
```

Permite especificar municipio y nombre descriptivo del motivo.

### Paso a paso interno del seed

```
1. Recolectar rutas de imágenes + metadatos
        ↓
2. Verificar cuáles ya están en BD (--skip-existing)
        ↓
3. Para cada imagen nueva:
   a. Abrir con PIL como RGB
   b. Extraer embedding EfficientNet-B0 → vector[1280]
   c. Si falla (archivo roto, modelo no cargado) → log warning + continuar
        ↓
4. Insertar en lotes de 50 (configurable)
   en la tabla image_embeddings
        ↓
5. Si la tabla tiene ≥ 100 filas:
   CREATE INDEX ivfflat (embedding vector_cosine_ops) WITH (lists=50)
```

### Por qué el índice IVFFlat requiere ≥ 100 filas

IVFFlat (Inverted File with Flat compression) divide el espacio vectorial en `lists=50` celdas de Voronoi usando k-means. Para calcular centroides representativos de esas celdas, se necesitan suficientes datos. Con menos de 100 filas el índice sería menos eficiente que una búsqueda lineal secuencial.

Una vez creado, las búsquedas de vecinos más cercanos son **O(k × n/lists)** en vez de O(n), con una pérdida de precisión controlada (búsqueda aproximada).

---

## 4. Fase 2 — Extracción de embeddings con EfficientNet-B0

**Archivo:** `adapters/outbound/embeddings/efficientnet_adapter.py`

### ¿Qué es EfficientNet-B0?

Es una red neuronal convolucional publicada por Google en 2019. Fue entrenada para clasificar 1000 categorías de ImageNet. Al eliminar su cabeza de clasificación (`num_classes=0`), lo que queda es un **extractor de características**: convierte cualquier imagen en un vector denso de 1280 números que captura su contenido visual de forma compacta.

La clave es que imágenes visualmente similares (mismas formas, mismas texturas, misma composición) producen vectores con **distancia coseno pequeña**, independientemente de diferencias de iluminación, escala o rotación moderada.

### Carga del modelo (una sola vez al iniciar)

```python
import timm
model = timm.create_model("efficientnet_b0", pretrained=True, num_classes=0)
model.eval()
```

- `pretrained=True`: pesos de ImageNet descargados automáticamente desde `timm` en la primera ejecución y cacheados localmente.
- `num_classes=0`: en vez de retornar 1000 probabilidades, retorna el vector de características global (salida del Global Average Pooling de la última capa convolucional, 1280 dims).
- `model.eval()`: desactiva Dropout y pone BatchNorm en modo inferencia (estadísticas fijas, no actualizadas). Imprescindible para resultados deterministas.

### Pipeline de transformación de imagen

Cada imagen pasa exactamente por estos pasos antes de entrar a la red:

```python
T.Resize((224, 224))
T.ToTensor()          # PIL → tensor float32 [C, H, W], valores en [0, 1]
T.Normalize(
    mean=[0.485, 0.456, 0.406],   # media de ImageNet por canal
    std=[0.229, 0.224, 0.225]     # desviación estándar de ImageNet
)
```

**¿Por qué normalizar con estadísticas de ImageNet?**
EfficientNet-B0 aprendió a extraer características con esas distribuciones de entrada. Si la imagen no está normalizada igual que en el entrenamiento, las activaciones internas son distintas y el vector resultante no es comparable con los del corpus.

### Extracción

```python
img = Image.open(path).convert("RGB")     # elimina canal alpha si existe
tensor = transform(img).unsqueeze(0)       # añade dimensión batch: [1, 3, 224, 224]
with torch.no_grad():                      # sin calcular gradientes
    features = model(tensor)               # → [1, 1280]
return features.squeeze().numpy().tolist() # → lista de 1280 floats
```

`torch.no_grad()` es crítico en producción: sin él PyTorch acumula el grafo computacional para calcular gradientes del backpropagation, lo que triplica el uso de memoria y reduce la velocidad sin ningún beneficio.

---

## 5. Fase 3 — Almacenamiento vectorial en pgvector

**Archivo:** `adapters/outbound/vector_store/pgvector_adapter.py`

### ¿Qué es pgvector?

Es una extensión de PostgreSQL que añade:
- Un tipo de columna `VECTOR(n)` para almacenar vectores de `n` floats.
- Operadores de distancia: `<=>` (coseno), `<->` (euclidiana), `<#>` (producto interno).
- Índices especializados: `ivfflat` y `hnsw`.

La ventaja sobre una solución separada (Pinecone, Weaviate, etc.) es que los vectores viven en la misma BD que el resto de los datos, con las mismas garantías transaccionales.

### Inserción de embeddings (upsert)

Cada registro del corpus se inserta como una fila en `image_embeddings`:

```python
session.add(ImageEmbedding(
    site_name="Piedras del Tunjo",
    taxonomy="Geométrico",
    reference_name="Espiral central",
    image_path="storage/reference_images/...",
    embedding=[0.123, -0.045, 0.789, ...]  # lista de 1280 floats
))
```

SQLAlchemy serializa el campo `Vector(1280)` al formato de pgvector: `'[0.123,-0.045,0.789,...]'::vector`.

---

## 6. Fase 4 — Pipeline de comparación de una imagen nueva

**Archivo:** `orchestrator/comparator.py` · función `compare_image()`

Este es el **corazón del sistema**: el flujo que se ejecuta cada vez que llega una imagen nueva a través de `POST /compare`.

### Entrada

```json
{
  "image_path": "storage/nuevas/gameza_nuevo_01.jpg",
  "site": "Gámeza",
  "municipality": "Gámeza",
  "department": "Boyacá"
}
```

### Los 4 pasos del pipeline

#### Paso 1 — Extracción del embedding de la imagen nueva

```
imagen_nueva → EfficientNet-B0 → vector_consulta[1280]
```

Si el modelo no está cargado o el archivo no existe, el pipeline se detiene aquí y retorna `embedding_available: false`. Ningún dato se persiste.

#### Paso 2 — Búsqueda vectorial en el corpus

```
vector_consulta → pgvector similarity_search(k=5, min_sim=0.60)
               → lista de hasta 5 imágenes del corpus similares
```

Cada match contiene: `site_name`, `taxonomy`, `similarity_score`, `reference_name`, `image_path`.

El umbral `IMAGE_MIN_SIMILARITY=0.60` actúa como pre-filtro: descarta coincidencias que son mera semejanza fotográfica superficial sin relevancia arqueológica.

#### Paso 3 — Actualización del grafo en memoria

Para cada match con `similarity_score >= EDGE_MIN_SIMILARITY (0.70)`:

```python
graph.add_or_update_edge(
    site_a="Gámeza",          # sitio de la imagen nueva
    site_b="Piedras del Tunjo",  # sitio del match en corpus
    weight=0.83,
    taxonomy="Geométrico"
)
```

El grafo en memoria (NetworkX) se actualiza **inmediatamente**, antes de confirmar en BD. Esto permite respuestas en tiempo real sin esperar operaciones de disco.

#### Paso 4 — Persistencia en PostgreSQL

```python
edges_persisted = await _persist_edges(
    session=session,
    current_site_name="Gámeza",
    current_municipality="Gámeza",
    matches=matches
)
```

Las aristas se escriben a `site_graph_edges` con manejo de upsert manual.

### Respuesta del pipeline

```json
{
  "matches": [
    {"site_name": "Piedras del Tunjo", "taxonomy": "Geométrico",
     "similarity_score": 0.8312, "reference_name": "Espiral concéntrica"},
    {"site_name": "Villa de Leyva", ...}
  ],
  "graph_updated": true,
  "edges_persisted": 2,
  "latency_ms": 145,
  "embedding_available": true
}
```

---

## 7. Fase 5 — Búsqueda de similitud coseno

**Archivo:** `adapters/outbound/vector_store/pgvector_adapter.py` · método `similarity_search()`

### El operador `<=>` de pgvector

pgvector implementa la distancia coseno como:

```
distancia_coseno(A, B) = 1 - cos(θ) = 1 - (A · B) / (|A| × |B|)
```

Donde:
- `distancia = 0` → vectores idénticos (mismo ángulo, máxima similitud)
- `distancia = 1` → vectores ortogonales (sin similitud)
- `distancia = 2` → vectores opuestos (máxima disimilitud)

La similitud se obtiene como `1 - distancia`, lo que da valores entre 0 y 1.

### La consulta SQL

```sql
SELECT
    site_name, taxonomy, reference_name, image_path,
    1 - (embedding <=> '[0.123, -0.045, ...]'::vector) AS similarity
FROM image_embeddings
WHERE 1 - (embedding <=> '[0.123, -0.045, ...]'::vector) >= 0.60
ORDER BY embedding <=> '[0.123, -0.045, ...]'::vector
LIMIT 5
```

Aspectos técnicos importantes:

1. **El vector de consulta se embebe como literal SQL.** pgvector no soporta parámetros preparados para vectores con PgBouncer (el pooler de Supabase). Por eso se construye el SQL con f-string e interpolación directa del vector, y `statement_cache_size=0` en el engine evita que SQLAlchemy intente preparar el statement.

2. **ORDER BY usa la distancia (menor es mejor)** mientras el WHERE usa la similitud (mayor es mejor). Son matemáticamente complementarios pero se escriben con distinta dirección.

3. **El índice IVFFlat** solo se usa en el ORDER BY/LIMIT. La cláusula WHERE sobre el resultado de `<=>` evita el escaneo completo al usar el índice para encontrar los candidatos más cercanos primero.

### Resultado

```python
[
    {
        "site_name": "Piedras del Tunjo",
        "municipality": "Facatativá",
        "taxonomy": "Geométrico",
        "reference_name": "Espiral central",
        "similarity_score": 0.8312,  # redondeado a 4 decimales
        "image_path": "storage/ref/tunjo_01.jpg"
    },
    ...
]
```

Solo se retornan hasta `IMAGE_TOP_K=5` resultados con similitud ≥ 0.60. Si no hay ninguno, la función retorna lista vacía y no se crea ninguna arista.

---

## 8. Fase 6 — Actualización del grafo en memoria

**Archivo:** `graphs/social_graph.py` · método `add_or_update_edge()`

### ¿Por qué un grafo en memoria además de la BD?

La BD es el almacenamiento persistente, pero reconstruirla en cada request de análisis (PageRank, comunidades) tendría latencia alta. El grafo NetworkX en memoria actúa como **caché computacional**: está siempre listo para algoritmos de grafos que necesitan múltiples traversals.

### Lógica de upsert en memoria

```python
if self._G.has_edge(site_a, site_b):
    # ACTUALIZAR: promedio ponderado acumulativo
    n = data["evidence_count"]             # observaciones anteriores
    data["weight"] = (data["weight"] * n + nuevo_score) / (n + 1)
    data["evidence_count"] = n + 1
    # añadir taxonomía si es nueva
    if taxonomy not in data["shared_taxonomies"]:
        data["shared_taxonomies"].append(taxonomy)
else:
    # CREAR nueva arista
    self._G.add_edge(site_a, site_b,
        weight=score,
        evidence_count=1,
        shared_taxonomies=[taxonomy]
    )
```

### El promedio ponderado acumulativo

Este algoritmo actualiza el promedio sin guardar el historial completo de observaciones:

```
promedio_nuevo = (promedio_viejo × n + score_nuevo) / (n + 1)
```

Propiedad clave: si hay 5 observaciones anteriores con promedio 0.82 y llega una nueva con score 0.78:

```
nuevo = (0.82 × 5 + 0.78) / 6 = 0.8133
```

El promedio se desplaza suavemente hacia el nuevo valor. Observaciones antiguas tienen más peso proporcionalmente porque son la mayoría. Esto da **estabilidad**: una sola observación atípica no derrumba la arista.

### Cálculo de is_provisional

Inmediatamente después de cada actualización:

```python
data["is_provisional"] = not (
    data["weight"] >= settings.edge_reliable_min_similarity   # 0.76
    and data["evidence_count"] >= settings.edge_min_evidence   # 2
)
```

La arista pasa de provisional a confiable cuando **ambas condiciones se cumplen simultáneamente**: alta similitud promedio Y suficiente evidencia repetida.

---

## 9. Fase 7 — Persistencia de aristas en PostgreSQL

**Archivo:** `orchestrator/comparator.py` · función `_persist_edges()`

### Resolución de UUIDs de sitios

Los matches de pgvector retornan nombres de sitios (`"Piedras del Tunjo"`), pero la tabla `site_graph_edges` usa UUIDs como foreign keys. El paso previo a la persistencia resuelve este mapeo:

```python
site_a_uuid = await _get_or_create_site(session, "Gámeza", "Gámeza")
site_b_uuid = await _get_or_create_site(session, "Piedras del Tunjo", "Facatativá")
```

`_get_or_create_site` implementa **get-or-create con manejo de race condition**:

```python
# 1. intentar leer
result = await session.execute(
    select(RupestranSiteModel).where(name == site_name)
)
if result: return result.id

# 2. si no existe, crear
try:
    new_site = RupestranSiteModel(name=site_name, municipality=municipality)
    session.add(new_site)
    await session.flush()
    return new_site.id
except IntegrityError:
    # 3. otro request creó el sitio concurrentemente → hacer retry y leer el ya existente
    await session.rollback()
    result = await session.execute(select(...).where(name == site_name))
    return result.scalar_one_or_none().id
```

Esto maneja el escenario donde dos requests concurrentes intentan crear el mismo sitio simultáneamente: el segundo hilo recibe `IntegrityError` por la constraint `UNIQUE(name)` y hace fallback a leer el registro que ya creó el primero.

### Ordenación canónica de UUIDs

Para garantizar la unicidad de la arista independientemente de la dirección de la comparación:

```python
id_a, id_b = sorted([site_a_uuid, site_b_uuid])
```

Si hoy comparo imagen de Gámeza contra corpus y mañana comparo imagen de Piedras del Tunjo contra el mismo corpus, ambas comparaciones pueden detectar la misma relación de similitud. Sin la ordenación, se crearían dos aristas `(Gámeza → Tunjo)` y `(Tunjo → Gámeza)`. Con la ordenación, siempre hay exactamente una arista entre cada par, que se va enriqueciendo.

### Upsert manual (UPDATE o INSERT)

```python
existing = await session.execute(
    select(SiteGraphEdge).where(
        SiteGraphEdge.site_a_id == id_a,
        SiteGraphEdge.site_b_id == id_b,
    )
).scalar_one_or_none()

if existing:
    # ACTUALIZAR: mismo promedio ponderado que el grafo en memoria
    n = existing.evidence_count
    existing.weight = (existing.weight * n + score) / (n + 1)
    existing.evidence_count = n + 1
    existing.shared_taxonomies = acumular_taxonomias(existing, taxonomy)
    existing.is_provisional = not (weight >= 0.76 and evidence_count >= 2)
else:
    # INSERTAR primera observación
    session.add(SiteGraphEdge(
        site_a_id=id_a, site_b_id=id_b,
        weight=score,
        evidence_count=1,
        is_provisional=True,   # siempre provisional al nacer
    ))
```

Al final del pipeline, `session.flush()` envía los cambios al servidor PostgreSQL dentro de la transacción activa. El `commit()` real lo hace el `get_session()` de FastAPI al terminar el request sin error.

---

## 10. Fase 8 — Sistema de confianza de aristas

**Archivos:** `config/settings.py` · `graphs/social_graph.py` · `infrastructure/database/models/models.py`

### El problema que resuelve

Con un solo umbral de similitud (0.70), el grafo acumulaba **falsos positivos**: dos imágenes de sitios distintos que comparten superficialmente el mismo tipo de fondo rocoso, la misma iluminación o el mismo fotógrafo podían generar una arista aunque los motivos no tuvieran relación arqueológica real.

La solución es el **doble criterio**: una arista solo se considera "confiable" cuando ha sido confirmada por múltiples comparaciones independientes.

### Las tres capas del sistema

#### Capa 1 — Umbral de creación (0.70)

```python
EDGE_MIN_SIMILARITY = 0.70
```

Filtro de entrada. Solo similitudes por encima de este valor crean o actualizan aristas. Descarta matches claramente irrelevantes (similitud fotográfica sin contenido arqueológico).

#### Capa 2 — Doble criterio de confiabilidad

```python
EDGE_RELIABLE_MIN_SIMILARITY = 0.76   # umbral alto de similitud promedio
EDGE_MIN_EVIDENCE = 2                  # mínimo de comparaciones independientes

is_provisional = not (
    weight >= 0.76
    and evidence_count >= 2
)
```

Una arista es **confiable** solo cuando cumple ambas condiciones:
- El promedio acumulado de todas sus similitudes supera 0.76.
- Al menos dos comparaciones independientes la han confirmado.

#### Capa 3 — Clasificación de confianza (confidence_level)

```python
def _compute_confidence_level(weight, evidence_count):
    if weight >= 0.85 and evidence_count >= 3:
        return "high"     # conexión fuerte y repetidamente confirmada
    if weight >= 0.76 and evidence_count >= 2:
        return "medium"   # confiable pero sin excess de evidencia
    return "low"           # provisional: evidencia insuficiente
```

| Nivel | Significado arqueológico |
|---|---|
| `low` | Observación aislada o similitud borderline; posible ruido |
| `medium` | Similitud real confirmada; útil para análisis |
| `high` | Conexión robusta con múltiples evidencias; altamente confiable |

### Ciclo de vida de una arista

```
Primera comparación (score=0.78):
  INSERT evidence_count=1, weight=0.7800
  is_provisional=True, confidence_level="low"
  → Arista existe pero no entra al ranking

Segunda comparación (score=0.81):
  UPDATE weight=(0.78×1 + 0.81)/2 = 0.7950, evidence_count=2
  0.7950 >= 0.76 AND 2 >= 2 → is_provisional=False
  confidence_level="medium"
  → Arista entra al PageRank y comunidades

Tercera comparación (score=0.87):
  UPDATE weight=(0.795×2 + 0.87)/3 = 0.8200, evidence_count=3
  confidence_level="medium" (0.82 < 0.85 o necesita más evidencias)

Décima comparación (promedio acumulado=0.86, evidence_count=10):
  confidence_level="high"
  → Conexión arqueológica sólidamente establecida
```

### Impacto en los algoritmos

| Algoritmo | Usa aristas provisionales |
|---|---|
| `pagerank()` | ❌ Solo confiables |
| `communities()` | ❌ Solo confiables |
| `betweenness_centrality()` | ❌ Solo confiables |
| `most_similar_sites()` | ✅ Todas (pero marcadas) |
| `GET /graph` | ✅ Todas (con `confidence_level`) |
| `GET /graph/metrics` | ✅ Todas |

Los algoritmos analíticos operan sobre `_reliable_subgraph()`: una **vista inmutable** de NetworkX que incluye solo aristas donde `is_provisional=False`. Al ser una vista (no copia), no duplica datos en memoria.

---

## 11. Fase 9 — Reconstrucción del grafo desde la BD

**Archivo:** `adapters/inbound/api/main.py` · función `_build_graph_from_db()`

### Cuándo ocurre

- Al **iniciar la API**: el evento `@app.on_event("startup")` carga todas las aristas existentes en el grafo en memoria global `_graph`.
- En cada **request de análisis** (`/pagerank`, `/communities`, etc.): se reconstruye el grafo para garantizar datos frescos.

### El proceso

```python
# 1. Cargar todos los sitios (nodos)
sites = await session.execute(select(RupestranSiteModel)).scalars().all()
for site in sites:
    graph.add_site(site.name, municipality=..., department=..., ...)

# 2. Cargar todas las aristas con su estado persistido
edges = await session.execute(select(SiteGraphEdge)).scalars().all()
id_to_name = {s.id: s.name for s in sites}

for edge in edges:
    name_a = id_to_name[edge.site_a_id]
    name_b = id_to_name[edge.site_b_id]
    graph.load_persisted_edge(
        name_a, name_b,
        weight=edge.weight,
        evidence_count=edge.evidence_count,
        shared_taxonomies=edge.shared_taxonomies,
        is_provisional=edge.is_provisional,   # estado ya calculado en BD
    )
```

### El método `load_persisted_edge` vs `add_or_update_edge`

Este es un punto técnico importante. **No se puede usar `add_or_update_edge` para reconstruir desde BD** porque ese método está diseñado para procesar una observación nueva: si la arista ya existe, incrementa `evidence_count` y recalcula el promedio.

Si reconstruimos una arista con `evidence_count=5` y `weight=0.83` usando `add_or_update_edge`, el método interpretaría eso como una nueva observación con `score=0.83` sobre una arista que ya tenía `evidence_count=1`, dejando el estado corrupto.

`load_persisted_edge` simplemente hace `self._G.add_edge(...)` con los valores exactos tal como están en la BD, sin ningún cálculo adicional.

---

## 12. Fase 10 — Algoritmos de análisis del grafo

**Archivo:** `graphs/social_graph.py`

Una vez el grafo está en memoria con todas sus aristas y el estado correcto de `is_provisional`, se pueden ejecutar los algoritmos de análisis de redes.

### Subgrafo confiable

```python
def _reliable_subgraph(self) -> nx.Graph:
    reliable = [(u, v) for u, v, d in self._G.edges(data=True)
                if not d.get("is_provisional", True)]
    return self._G.edge_subgraph(reliable)
```

`nx.Graph.edge_subgraph()` retorna una **vista lazy**: no copia nodos ni aristas, sino que crea un proxy que filtra en tiempo real. Usar esto en vez de crear un nuevo grafo ahorra memoria y tiempo de construcción.

### PageRank

```python
G = self._reliable_subgraph()
return nx.pagerank(G, alpha=0.85, weight="weight")
```

PageRank calcula la **importancia estructural** de cada nodo iterativamente:

```
PR(sitio) = (1 - alpha) / N + alpha × Σ [ PR(vecino) × weight(vecino→sitio) / Σ_weights(vecino) ]
```

- Un sitio tiene PageRank alto si muchos sitios similares apuntan a él.
- `alpha=0.85` (damping factor): el 85% del tiempo se sigue una arista del grafo; el 15% se salta a un nodo aleatorio. Garantiza la convergencia.
- Usa los pesos de las aristas (`weight="weight"`): similitudes más altas transfieren más "importancia".

**Interpretación arqueológica:** el sitio con mayor PageRank no es necesariamente el que tiene más conexiones, sino el que está más conectado a otros sitios bien conectados. Identifica "hubs iconográficos": sitios cuya iconografía es más representativa del estilo general de toda la red.

### Comunidades (Louvain)

```python
partition = best_partition(G, weight="weight")
```

El algoritmo Louvain busca la partición del grafo que maximiza la **modularidad**:

```
Q = (1/2m) × Σ [Aij - ki×kj/2m] × δ(ci, cj)
```

Donde `Aij` es el peso de la arista entre i y j, `ki` es la suma de pesos de todas las aristas del nodo i, `m` es la suma de todos los pesos, y `δ(ci, cj)` es 1 si i y j están en la misma comunidad.

En términos simples: agrupa sitios donde hay más similitud interna que la que habría si las aristas fueran aleatorias. El resultado es un conjunto de **grupos iconográficos regionales**: sitios que comparten un estilo particular, probablemente por proximidad geográfica o cultural.

### Centralidad de intermediación (betweenness)

```python
nx.betweenness_centrality(G, weight="weight", normalized=True)
```

Para cada nodo calcula qué fracción de los caminos más cortos entre todos los pares de nodos pasa por él:

```
BC(v) = Σ [σ(s,t|v) / σ(s,t)]   para todos los pares (s,t)
```

Donde `σ(s,t)` es el número de caminos más cortos entre `s` y `t`, y `σ(s,t|v)` los que pasan por `v`.

Con `weight="weight"` y NetworkX, los caminos más cortos se calculan como **menor suma de distancias** (donde distancia = 1/weight para que mayor similitud = menor distancia). Un sitio con betweenness alto es un **puente cultural**: conecta grupos iconográficos distintos que de lo contrario estarían desconectados entre sí.

---

## 13. Flujo de procesamiento en lote (bulk)

**Archivo:** `scripts/bulk_compare.py`

Para construir el grafo completo desde cero a partir de todo el corpus de imágenes, sin pasar por la API:

```bash
python -m scripts.bulk_compare --csv storage/reference_images/manifest.csv
```

### Funcionamiento

```
Leer CSV (columnas: image_path, site_name, municipality, department)
        ↓
Para cada imagen:
  1. Abrir sesión DB independiente
  2. Ejecutar compare_image() completo
  3. commit() o rollback() si hay error
  (Un error en una imagen no cancela las demás)
        ↓
Reporte cada 10 imágenes: progreso, aristas acumuladas
        ↓
Resumen final: nodos, aristas del grafo, aristas en BD
```

**Por qué sesiones independientes por imagen:** si se usara una sola sesión para todo el batch y una imagen fallara, el rollback cancelaría todas las inserciones anteriores. Con sesiones por imagen, el fallo es aislado.

### Diferencia con el flujo de la API

En la API, el grafo en memoria `_graph` es el global singleton de FastAPI que persiste entre requests. En `bulk_compare`, se crea un `PetroglyphSocialGraph()` local que solo existe durante la ejecución del script. Al terminar el script ese grafo en memoria se descarta; el estado persistente son las filas escritas en `site_graph_edges`.

La próxima vez que la API arranque, el evento `startup` reconstruye el grafo en memoria leyendo esas filas desde la BD.

---

## 14. Diagrama completo de flujo de datos

```
╔══════════════════════════════════════════════════════════════════╗
║  FASE DE SETUP (una sola vez)                                    ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  Imágenes de referencia                                          ║
║  (clasificadas por sitio/taxonomía)                              ║
║         │                                                        ║
║    [seed_embeddings.py]                                          ║
║         │                                                        ║
║         ▼                                                        ║
║  EfficientNet-B0 → vector[1280] por imagen                       ║
║         │                                                        ║
║         ▼                                                        ║
║  PostgreSQL: INSERT image_embeddings                             ║
║  (si ≥100 filas: CREATE INDEX ivfflat)                           ║
║                                                                  ║
╠══════════════════════════════════════════════════════════════════╣
║  FASE DE OPERACIÓN (por cada imagen nueva)                       ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  POST /compare {image_path, site, municipality, department}      ║
║         │                                                        ║
║    [efficientnet_adapter.py]                                     ║
║         │ Resize → ToTensor → Normalize(ImageNet)                ║
║         │ model.forward() con torch.no_grad()                    ║
║         ▼                                                        ║
║  vector_consulta[1280]                                           ║
║         │                                                        ║
║    [pgvector_adapter.py]                                         ║
║         │ SELECT ... WHERE 1-(embed<=>query) >= 0.60             ║
║         │ ORDER BY distancia LIMIT 5                             ║
║         ▼                                                        ║
║  matches [{site_name, taxonomy, similarity_score}, ...]          ║
║         │                                                        ║
║         ├──────────────────────────────────────┐                 ║
║         │  score >= 0.70?                      │                 ║
║         ▼                                      ▼                 ║
║  [social_graph.py]                       IGNORAR                 ║
║  add_or_update_edge(                     (no se persiste)        ║
║    site_a, site_b,                                               ║
║    weight, taxonomy                                              ║
║  )                                                               ║
║    ├── SI EXISTE: avg_ponderado(weight)                          ║
║    │              evidence_count++                               ║
║    └── SI NO:     weight, evidence_count=1                       ║
║         │                                                        ║
║         ▼                                                        ║
║  is_provisional = NOT(weight>=0.76 AND evid>=2)                  ║
║         │                                                        ║
║    [comparator.py: _persist_edges()]                             ║
║         │ _get_or_create_site(site_a)  → UUID                    ║
║         │ _get_or_create_site(site_b)  → UUID                    ║
║         │ sorted([uuid_a, uuid_b])     → orden canónico          ║
║         │                                                        ║
║         ├── SELECT SiteGraphEdge WHERE (a,b) ...                 ║
║         │                                                        ║
║         ├── EXISTS → UPDATE weight (avg), evidence_count++       ║
║         │            UPDATE is_provisional                       ║
║         │                                                        ║
║         └── NOT EXISTS → INSERT weight, evidence_count=1         ║
║                          is_provisional=True                     ║
║         │                                                        ║
║         ▼                                                        ║
║  session.flush() → session.commit() (get_session)                ║
║                                                                  ║
╠══════════════════════════════════════════════════════════════════╣
║  FASE DE ANÁLISIS (por cada request analítico)                   ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  GET /graph/pagerank                                             ║
║         │                                                        ║
║    [_build_graph_from_db()]                                      ║
║         │ SELECT rupestrian_sites   → add_site() × N             ║
║         │ SELECT site_graph_edges   → load_persisted_edge() × E  ║
║         │ (preserva weight, evidence_count, is_provisional)      ║
║         ▼                                                        ║
║  PetroglyphSocialGraph con grafo NetworkX completo               ║
║         │                                                        ║
║    [_reliable_subgraph()]                                        ║
║         │ filtra aristas donde is_provisional=False              ║
║         │ (100 de 154 en el estado actual)                       ║
║         ▼                                                        ║
║  subgrafo confiable (vista lazy NetworkX)                        ║
║         │                                                        ║
║  nx.pagerank(subgrafo, alpha=0.85, weight="weight")              ║
║         │                                                        ║
║         ▼                                                        ║
║  {"Piedras del Tunjo": 0.0512, "Gámeza": 0.0488, ...}           ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## 15. Estructura de datos a lo largo del pipeline

### En el corpus (table `image_embeddings`)

```
image_path    = "storage/ref/gameza/gameza_espiral.jpg"
site_name     = "Gámeza"
taxonomy      = "Geométrico"
reference_name = "Espiral triple"
embedding     = [0.1234, -0.0456, 0.7891, ..., 0.0023]  # 1280 floats
```

### Tras la búsqueda vectorial (dict de Python)

```python
{
    "site_name": "Piedras del Tunjo",
    "taxonomy": "Geométrico",
    "reference_name": "Espiral central",
    "similarity_score": 0.8312,
    "image_path": "storage/ref/tunjo/tunjo_espiral.jpg"
}
```

### En el grafo en memoria (NetworkX edge attributes)

```python
self._G["Gámeza"]["Piedras del Tunjo"] = {
    "weight": 0.8413,        # promedio acumulado de todas las similitudes
    "evidence_count": 3,     # 3 comparaciones han confirmado esta arista
    "shared_taxonomies": ["Geométrico", "Astronómico"],
    "is_provisional": False  # cumple doble criterio
}
```

### En la base de datos (table `site_graph_edges`)

```
site_a_id         = "f8abfdc1-b4d2-43c0-87d7-e101decc50f7"   (Piedras del Tunjo)
site_b_id         = "c3a23d8a-e92d-4552-be10-7798005de49a"   (Gámeza)
weight            = 0.8413
evidence_count    = 3
shared_taxonomies = ["Geométrico", "Astronómico"]
is_provisional    = false
updated_at        = "2026-05-30T14:15:13Z"
```

### En la respuesta de la API (`GET /sites/{id}`)

```json
{
  "connected_site_id": "c3a23d8a-e92d-4552-be10-7798005de49a",
  "weight": 0.8413,
  "evidence_count": 3,
  "shared_taxonomies": ["Geométrico", "Astronómico"],
  "is_provisional": false,
  "confidence_level": "medium"
}
```

---

*Documento técnico interno — Proyecto Grafos Sociales Rupestres, UPTC 2026.*
