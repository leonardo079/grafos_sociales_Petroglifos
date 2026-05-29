"""
Pipeline de comparación iconográfica — versión simplificada sin LangGraph.

Flujo: imagen → embedding EfficientNet-B0 → búsqueda pgvector → actualizar grafo → persistir aristas.
"""
from __future__ import annotations
import time
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.outbound.embeddings.efficientnet_adapter import extract_image_embedding
from adapters.outbound.vector_store.pgvector_adapter import ImageVectorAdapter
from graphs.social_graph import PetroglyphSocialGraph
from config.settings import settings

log = structlog.get_logger(__name__)


async def compare_image(
    image_path: str,
    site: str,
    municipality: str,
    department: str,
    session: AsyncSession,
    graph: PetroglyphSocialGraph,
) -> dict:
    """
    Compara una imagen contra el corpus de referencia en pgvector y actualiza el grafo social.

    Retorna un dict con los matches encontrados y estadísticas del proceso.
    """
    t0 = time.monotonic()

    # 1. Extraer embedding EfficientNet-B0
    embedding = extract_image_embedding(image_path)
    if embedding is None:
        log.warning("compare_image_no_embedding", path=image_path)
        return {
            "matches": [],
            "graph_updated": False,
            "edges_persisted": 0,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "embedding_available": False,
        }

    # 2. Buscar similitudes en pgvector
    adapter = ImageVectorAdapter(session)
    raw_matches = await adapter.similarity_search(
        query_vector=embedding,
        k=settings.image_top_k,
        min_similarity=settings.image_min_similarity,
    )
    matches = [
        {
            "site_name": m["site_name"],
            "municipality": m["municipality"],
            "reference_name": m["reference_name"],
            "taxonomy": m["taxonomy"],
            "similarity_score": m["similarity_score"],
            "image_path": m["image_path"],
        }
        for m in raw_matches
    ]

    # 3. Actualizar grafo en memoria (solo aristas con score >= edge_min_similarity)
    edges_in_memory = 0
    if site and matches:
        for match in matches:
            node_b = match.get("site_name", "")
            if node_b and match["similarity_score"] >= settings.edge_min_similarity:
                graph.add_or_update_edge(
                    site_a=site,
                    site_b=node_b,
                    weight=match["similarity_score"],
                    taxonomy=match.get("taxonomy", ""),
                )
                edges_in_memory += 1

    # 4. Persistir aristas en site_graph_edges
    edges_persisted = 0
    if site and matches:
        edges_persisted = await _persist_edges(
            session=session,
            current_site_name=site,
            current_municipality=municipality,
            matches=matches,
        )

    elapsed = int((time.monotonic() - t0) * 1000)
    log.info(
        "compare_image_done",
        site=site,
        matches=len(matches),
        edges_in_memory=edges_in_memory,
        edges_persisted=edges_persisted,
        latency_ms=elapsed,
    )

    return {
        "matches": matches,
        "graph_updated": edges_in_memory > 0,
        "edges_persisted": edges_persisted,
        "latency_ms": elapsed,
        "embedding_available": True,
    }


async def _get_or_create_site(session: AsyncSession, name: str, municipality: str = "") -> str | None:
    """Retorna el UUID del sitio por nombre, creándolo si no existe."""
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    from infrastructure.database.models.models import RupestranSiteModel

    result = await session.execute(
        select(RupestranSiteModel).where(RupestranSiteModel.name == name).limit(1)
    )
    site = result.scalar_one_or_none()
    if site:
        return site.id

    try:
        new_site = RupestranSiteModel(name=name, municipality=municipality)
        session.add(new_site)
        await session.flush()
        log.debug("site_auto_created", name=name)
        return new_site.id
    except IntegrityError:
        await session.rollback()
        result = await session.execute(
            select(RupestranSiteModel).where(RupestranSiteModel.name == name).limit(1)
        )
        existing = result.scalar_one_or_none()
        return existing.id if existing else None


async def _persist_edges(
    session: AsyncSession,
    current_site_name: str,
    current_municipality: str,
    matches: list[dict],
) -> int:
    """
    Persiste aristas de similitud (score >= edge_min_similarity) en site_graph_edges.
    Usa upsert manual con promedio acumulativo del peso.
    Retorna el número de aristas escritas.
    """
    from sqlalchemy import select, and_
    from infrastructure.database.models.models import RupestranSiteModel, SiteGraphEdge

    try:
        site_a_uuid = await _get_or_create_site(session, current_site_name, current_municipality)
        if not site_a_uuid:
            return 0

        persisted = 0
        for match in matches:
            score = match.get("similarity_score", 0.0)
            if score < settings.edge_min_similarity:
                continue

            match_name = match.get("site_name", "")
            if not match_name:
                continue

            site_b_uuid = await _get_or_create_site(
                session, match_name, match.get("municipality", "")
            )
            if not site_b_uuid or site_a_uuid == site_b_uuid:
                continue

            taxonomy = match.get("taxonomy", "")
            id_a, id_b = sorted([site_a_uuid, site_b_uuid])

            existing = (await session.execute(
                select(SiteGraphEdge).where(
                    and_(
                        SiteGraphEdge.site_a_id == id_a,
                        SiteGraphEdge.site_b_id == id_b,
                    )
                )
            )).scalar_one_or_none()

            if existing:
                n = existing.evidence_count
                existing.weight = round((existing.weight * n + score) / (n + 1), 4)
                existing.evidence_count = n + 1
                current_taxes = list(existing.shared_taxonomies or [])
                if taxonomy and taxonomy not in current_taxes:
                    existing.shared_taxonomies = current_taxes + [taxonomy]
            else:
                session.add(SiteGraphEdge(
                    site_a_id=id_a,
                    site_b_id=id_b,
                    weight=round(score, 4),
                    shared_taxonomies=[taxonomy] if taxonomy else [],
                    evidence_count=1,
                ))
            persisted += 1

        await session.flush()
        log.info("graph_edges_persisted", site=current_site_name, count=persisted)
        return persisted

    except Exception as exc:
        log.error("graph_persist_error", site=current_site_name, error=str(exc))
        return 0
