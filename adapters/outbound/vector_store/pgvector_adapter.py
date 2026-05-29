"""Búsqueda de similitud coseno sobre image_embeddings con pgvector."""
from __future__ import annotations
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from config.settings import settings

log = structlog.get_logger(__name__)


class ImageVectorAdapter:
    """Cosine similarity search sobre la tabla image_embeddings (1280 dims, EfficientNet-B0)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def similarity_search(
        self,
        query_vector: list[float],
        k: int | None = None,
        min_similarity: float | None = None,
    ) -> list[dict]:
        k = k or settings.image_top_k
        min_sim = min_similarity if min_similarity is not None else settings.image_min_similarity

        vec_literal = str(query_vector)
        sql = text(f"""
            SELECT
                id,
                petroglyph_id,
                site_name,
                municipality,
                reference_name,
                taxonomy,
                image_path,
                1 - (embedding <=> '{vec_literal}'::vector) AS similarity
            FROM image_embeddings
            WHERE 1 - (embedding <=> '{vec_literal}'::vector) >= :min_sim
            ORDER BY embedding <=> '{vec_literal}'::vector
            LIMIT :k
        """)
        result = await self._session.execute(sql, {"min_sim": min_sim, "k": k})
        rows = result.fetchall()
        log.debug("pgvector_image_search", k=k, results=len(rows), min_sim=min_sim)
        return [
            {
                "id": str(row.id),
                "petroglyph_id": str(row.petroglyph_id) if row.petroglyph_id else None,
                "site_name": row.site_name,
                "municipality": row.municipality,
                "reference_name": row.reference_name,
                "taxonomy": row.taxonomy,
                "image_path": row.image_path,
                "similarity_score": round(float(row.similarity), 4),
            }
            for row in rows
        ]

    async def upsert(self, records: list[dict]) -> None:
        """Inserta embeddings de imágenes de referencia en la tabla."""
        from infrastructure.database.models.models import ImageEmbedding

        for rec in records:
            self._session.add(ImageEmbedding(
                petroglyph_id=rec.get("petroglyph_id"),
                site_name=rec.get("site_name", ""),
                municipality=rec.get("municipality", ""),
                reference_name=rec.get("reference_name", ""),
                taxonomy=rec.get("taxonomy", "Indeterminado"),
                image_path=rec.get("image_path", ""),
                embedding=rec["embedding"],
                metadata_=rec.get("metadata", {}),
            ))
        await self._session.commit()
        log.info("image_embeddings_upsert", count=len(records))
