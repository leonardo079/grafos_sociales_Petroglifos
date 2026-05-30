"""Modelos ORM — solo las 3 tablas del módulo de grafos sociales."""
from __future__ import annotations
from datetime import datetime
from uuid import uuid4
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector
from infrastructure.database.session import Base


def _uuid() -> str:
    return str(uuid4())


# ─── Sitios rupestres (nodos del grafo) ──────────────────────────────────────

class RupestranSiteModel(Base):
    __tablename__ = "rupestrian_sites"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True)
    municipality: Mapped[str] = mapped_column(sa.String(255), default="")
    department: Mapped[str] = mapped_column(sa.String(255), default="")
    latitude: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    conservation_status: Mapped[str] = mapped_column(sa.String(50), default="Regular")
    dominant_taxonomy: Mapped[str] = mapped_column(sa.String(100), default="Indeterminado")
    petroglyph_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, default=datetime.utcnow)


# ─── Embeddings de imágenes (corpus de referencia para pgvector) ─────────────

class ImageEmbedding(Base):
    __tablename__ = "image_embeddings"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # petroglyph_id sin FK: este módulo no gestiona la tabla petroglyphs
    petroglyph_id: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    site_name: Mapped[str] = mapped_column(sa.String(255), default="")
    municipality: Mapped[str] = mapped_column(sa.String(255), default="")
    reference_name: Mapped[str] = mapped_column(sa.String(255), default="")
    taxonomy: Mapped[str] = mapped_column(sa.String(100), default="Indeterminado")
    image_path: Mapped[str] = mapped_column(sa.Text, default="")
    embedding: Mapped[list[float]] = mapped_column(Vector(1280), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, default=datetime.utcnow)

    __table_args__ = (
        sa.Index(
            "ix_img_embeddings_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 50},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# ─── Aristas del grafo social (similitud entre sitios) ───────────────────────

class SiteGraphEdge(Base):
    __tablename__ = "site_graph_edges"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    site_a_id: Mapped[str] = mapped_column(sa.ForeignKey("rupestrian_sites.id"), nullable=False)
    site_b_id: Mapped[str] = mapped_column(sa.ForeignKey("rupestrian_sites.id"), nullable=False)
    weight: Mapped[float] = mapped_column(sa.Float, default=0.0)
    shared_taxonomies: Mapped[list] = mapped_column(JSONB, default=list)
    evidence_count: Mapped[int] = mapped_column(sa.Integer, default=1)
    is_provisional: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        sa.UniqueConstraint("site_a_id", "site_b_id", name="uq_site_graph_edge"),
    )
