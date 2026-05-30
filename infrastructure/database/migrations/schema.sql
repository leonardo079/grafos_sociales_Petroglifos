-- ============================================================
-- schema.sql — Módulo de Grafos Sociales
-- Supabase ya tiene pgvector habilitado; no se necesita CREATE EXTENSION.
-- Ejecutar una sola vez contra la instancia Supabase del proyecto.
-- ============================================================

-- ── Sitios rupestres (nodos del grafo) ───────────────────────
CREATE TABLE IF NOT EXISTS rupestrian_sites (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    municipality        TEXT NOT NULL DEFAULT '',
    department          TEXT NOT NULL DEFAULT '',
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    conservation_status TEXT DEFAULT 'Regular',
    dominant_taxonomy   TEXT DEFAULT 'Indeterminado',
    petroglyph_count    INTEGER DEFAULT 0,
    metadata            JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_rupestrian_site_name UNIQUE (name)
);

-- ── Embeddings de imágenes (corpus de referencia) ────────────
-- petroglyph_id es opcional: permite referencias cruzadas al módulo
-- principal sin exigir que la tabla petroglyphs exista aquí.
CREATE TABLE IF NOT EXISTS image_embeddings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    petroglyph_id   TEXT,
    site_name       TEXT DEFAULT '',
    municipality    TEXT DEFAULT '',
    reference_name  TEXT DEFAULT '',
    taxonomy        TEXT DEFAULT 'Indeterminado',
    image_path      TEXT DEFAULT '',
    embedding       VECTOR(1280),
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Índice IVFFlat: crear DESPUÉS de tener >= 100 filas (lo hace seed_embeddings.py)
-- CREATE INDEX IF NOT EXISTS ix_img_embeddings_embedding
--     ON image_embeddings
--     USING ivfflat (embedding vector_cosine_ops)
--     WITH (lists = 50);

-- ── Aristas del grafo social ─────────────────────────────────
CREATE TABLE IF NOT EXISTS site_graph_edges (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_a_id         UUID NOT NULL REFERENCES rupestrian_sites(id),
    site_b_id         UUID NOT NULL REFERENCES rupestrian_sites(id),
    weight            FLOAT DEFAULT 0.0,
    shared_taxonomies JSONB DEFAULT '[]',
    evidence_count    INTEGER DEFAULT 1,
    is_provisional    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_site_graph_edge UNIQUE (site_a_id, site_b_id)
);

-- Trigger para actualizar updated_at en site_graph_edges
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_site_graph_edges_updated_at
    BEFORE UPDATE ON site_graph_edges
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
