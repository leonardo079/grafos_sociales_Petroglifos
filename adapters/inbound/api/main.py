"""
API FastAPI — Módulo de Grafos Sociales Rupestres.

Endpoints:
    POST /compare                      — Compara imagen contra corpus, actualiza grafo
    GET  /sites                        — Lista sitios rupestres registrados
    GET  /sites/{site_id}              — Detalle de un sitio con sus conexiones iconográficas
    GET  /graph                        — Red social de similitud iconográfica (JSON)
    GET  /graph/export                 — HTML interactivo del grafo (PyVis)
    GET  /graph/pagerank               — Ranking PageRank por sitio
    GET  /graph/communities            — Comunidades iconográficas (Louvain)
    GET  /graph/betweenness            — Centralidad de intermediación (sitios puente)
    GET  /graph/metrics                — Métricas de topología del grafo
    GET  /graph/sites/{site_id}/similar — Sitios más similares a uno dado
    GET  /health                       — Health check
"""
from __future__ import annotations
from pathlib import Path

import structlog
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from infrastructure.database.session import get_session
from graphs.social_graph import PetroglyphSocialGraph, _compute_confidence_level

log = structlog.get_logger(__name__)

app = FastAPI(
    title="Grafos Sociales Rupestres",
    version="1.0.0",
    description="Red de similitud iconográfica entre sitios arqueológicos — UPTC 2026",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas ────────────────────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    image_path: str
    site: str = "Sin nombre"
    municipality: str = ""
    department: str = ""


class CompareResponse(BaseModel):
    matches: list[dict] = []
    graph_updated: bool = False
    edges_persisted: int = 0
    latency_ms: int = 0
    embedding_available: bool = False


class SiteResponse(BaseModel):
    id: str
    name: str
    municipality: str
    department: str
    dominant_taxonomy: str
    petroglyph_count: int
    conservation_status: str


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Sistema"])
async def health_check(session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "version": "1.0.0",
        "environment": settings.env,
        "database": db_status,
    }


# ── Pipeline de comparación ────────────────────────────────────────────────────

# Grafo en memoria compartido entre requests (reconstruido desde BD al arrancar)
_graph: PetroglyphSocialGraph = PetroglyphSocialGraph()


@app.on_event("startup")
async def _preload_graph() -> None:
    """Reconstruye el grafo desde la BD al iniciar la API."""
    from infrastructure.database.session import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        global _graph
        _graph = await _build_graph_from_db(session)
    log.info(
        "graph_preloaded",
        nodes=len(list(_graph._G.nodes)),
        edges=len(list(_graph._G.edges)),
    )


@app.post("/compare", response_model=CompareResponse, tags=["Comparación"])
async def compare(
    payload: CompareRequest,
    session: AsyncSession = Depends(get_session),
) -> CompareResponse:
    """
    Extrae el embedding de la imagen con EfficientNet-B0, busca similitudes en pgvector
    y actualiza el grafo social con las aristas encontradas.
    """
    from orchestrator.comparator import compare_image

    result = await compare_image(
        image_path=payload.image_path,
        site=payload.site,
        municipality=payload.municipality,
        department=payload.department,
        session=session,
        graph=_graph,
    )
    return CompareResponse(**result)


# ── Sitios rupestres ───────────────────────────────────────────────────────────

@app.get("/sites", response_model=list[SiteResponse], tags=["Sitios"])
async def list_sites(
    session: AsyncSession = Depends(get_session),
    department: str | None = None,
    municipality: str | None = None,
) -> list[SiteResponse]:
    """Lista todos los sitios rupestres registrados. Soporta filtro por departamento/municipio."""
    from infrastructure.database.models.models import RupestranSiteModel

    stmt = select(RupestranSiteModel)
    if department:
        stmt = stmt.where(RupestranSiteModel.department.ilike(f"%{department}%"))
    if municipality:
        stmt = stmt.where(RupestranSiteModel.municipality.ilike(f"%{municipality}%"))

    result = await session.execute(stmt)
    sites = result.scalars().all()
    return [
        SiteResponse(
            id=s.id,
            name=s.name,
            municipality=s.municipality,
            department=s.department,
            dominant_taxonomy=s.dominant_taxonomy,
            petroglyph_count=s.petroglyph_count,
            conservation_status=s.conservation_status,
        )
        for s in sites
    ]


@app.get("/sites/{site_id}", tags=["Sitios"])
async def get_site(
    site_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Retorna detalle de un sitio incluyendo sus conexiones iconográficas en el grafo."""
    from infrastructure.database.models.models import RupestranSiteModel, SiteGraphEdge

    result = await session.execute(
        select(RupestranSiteModel).where(RupestranSiteModel.id == site_id)
    )
    site = result.scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail=f"Sitio {site_id} no encontrado.")

    edges_result = await session.execute(
        select(SiteGraphEdge).where(
            (SiteGraphEdge.site_a_id == site_id) | (SiteGraphEdge.site_b_id == site_id)
        )
    )
    edges = edges_result.scalars().all()
    connections = [
        {
            "connected_site_id": e.site_b_id if e.site_a_id == site_id else e.site_a_id,
            "weight": e.weight,
            "evidence_count": e.evidence_count,
            "shared_taxonomies": e.shared_taxonomies,
            "is_provisional": e.is_provisional,
            "confidence_level": _compute_confidence_level(e.weight, e.evidence_count),
        }
        for e in edges
    ]

    return {
        "id": site.id,
        "name": site.name,
        "municipality": site.municipality,
        "department": site.department,
        "latitude": site.latitude,
        "longitude": site.longitude,
        "dominant_taxonomy": site.dominant_taxonomy,
        "petroglyph_count": site.petroglyph_count,
        "conservation_status": site.conservation_status,
        "iconographic_connections": connections,
    }


# ── Grafo social ───────────────────────────────────────────────────────────────

async def _build_graph_from_db(session: AsyncSession) -> PetroglyphSocialGraph:
    """Reconstruye el grafo completo desde site_graph_edges y rupestrian_sites."""
    from infrastructure.database.models.models import RupestranSiteModel, SiteGraphEdge

    sites = list((await session.execute(select(RupestranSiteModel))).scalars().all())
    edges = list((await session.execute(select(SiteGraphEdge))).scalars().all())
    id_to_name: dict = {s.id: s.name for s in sites}

    graph = PetroglyphSocialGraph()
    for site in sites:
        graph.add_site(
            site.name,
            municipality=site.municipality,
            department=site.department,
            dominant_taxonomy=site.dominant_taxonomy,
            petroglyph_count=site.petroglyph_count,
            latitude=site.latitude,
            longitude=site.longitude,
        )
    for edge in edges:
        name_a = id_to_name.get(edge.site_a_id)
        name_b = id_to_name.get(edge.site_b_id)
        if name_a and name_b:
            graph.load_persisted_edge(
                name_a,
                name_b,
                weight=edge.weight,
                evidence_count=edge.evidence_count,
                shared_taxonomies=edge.shared_taxonomies,
                is_provisional=edge.is_provisional,
            )

    return graph


@app.get("/graph", tags=["Grafo Social"])
async def get_graph(session: AsyncSession = Depends(get_session)) -> dict:
    """Retorna el grafo de similitud iconográfica completo en formato JSON."""
    graph = await _build_graph_from_db(session)
    return graph.to_dict()


@app.get("/graph/export", tags=["Grafo Social"])
async def export_graph_html(session: AsyncSession = Depends(get_session)) -> FileResponse:
    """Exporta visualización interactiva con PyVis (fondo oscuro, física de partículas)."""
    graph = await _build_graph_from_db(session)
    html_path = graph.export_html()
    if not html_path or not Path(html_path).exists():
        raise HTTPException(status_code=500, detail="Error generando el grafo HTML.")
    return FileResponse(
        html_path,
        media_type="text/html",
        filename="red_rupestre.html",
        content_disposition_type="inline",  # renderiza en el navegador en vez de descargar
    )


@app.get("/graph/export/plotly", tags=["Grafo Social"])
async def export_graph_plotly(session: AsyncSession = Depends(get_session)) -> FileResponse:
    """Exporta visualización clara con Plotly — nodos etiquetados, colores por comunidad, similitud en aristas."""
    graph = await _build_graph_from_db(session)
    html_path = graph.export_plotly()
    if not html_path or not Path(html_path).exists():
        raise HTTPException(status_code=500, detail="Error generando el grafo Plotly.")
    return FileResponse(
        html_path,
        media_type="text/html",
        filename="red_rupestre_plotly.html",
        content_disposition_type="inline",  # renderiza en el navegador en vez de descargar
    )


@app.get("/graph/pagerank", tags=["Grafo Social"])
async def get_graph_pagerank(session: AsyncSession = Depends(get_session)) -> dict:
    """Ranking PageRank de los sitios — los más centrales en la red iconográfica."""
    graph = await _build_graph_from_db(session)
    pr = graph.pagerank()
    if not pr:
        return {"pagerank": {}, "top_site": None, "message": "Grafo sin datos suficientes"}
    sorted_pr = sorted(pr.items(), key=lambda x: x[1], reverse=True)
    return {
        "pagerank": {site: round(score, 6) for site, score in sorted_pr},
        "top_site": sorted_pr[0][0],
    }


@app.get("/graph/communities", tags=["Grafo Social"])
async def get_graph_communities(session: AsyncSession = Depends(get_session)) -> dict:
    """Comunidades iconográficas detectadas con Louvain — agrupa sitios con alta similitud estilística."""
    graph = await _build_graph_from_db(session)
    communities = graph.communities()
    return {
        "communities": [sorted(list(c)) for c in communities],
        "count": len(communities),
    }


@app.get("/graph/betweenness", tags=["Grafo Social"])
async def get_graph_betweenness(session: AsyncSession = Depends(get_session)) -> dict:
    """Centralidad de intermediación — sitios que actúan como puentes entre regiones rupestres."""
    graph = await _build_graph_from_db(session)
    bc = graph.betweenness_centrality()
    if not bc:
        return {"betweenness": {}, "top_bridge_site": None, "message": "Grafo sin aristas"}
    sorted_bc = sorted(bc.items(), key=lambda x: x[1], reverse=True)
    return {
        "betweenness": {site: round(score, 6) for site, score in sorted_bc},
        "top_bridge_site": sorted_bc[0][0],
    }


@app.get("/graph/metrics", tags=["Grafo Social"])
async def get_graph_metrics(session: AsyncSession = Depends(get_session)) -> dict:
    """
    Métricas de topología del grafo:
    clustering coefficient, componentes conectados, diámetro y distribución de grado.
    """
    graph = await _build_graph_from_db(session)
    return graph.metrics()


@app.get("/graph/sites/{site_id}/similar", tags=["Grafo Social"])
async def get_similar_sites(
    site_id: str,
    top_k: int = 5,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Top-k sitios más similares iconográficamente a uno dado (por UUID)."""
    from infrastructure.database.models.models import RupestranSiteModel

    site_result = await session.execute(
        select(RupestranSiteModel).where(RupestranSiteModel.id == site_id)
    )
    site = site_result.scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail=f"Sitio {site_id} no encontrado.")

    graph = await _build_graph_from_db(session)
    similar = graph.most_similar_sites(site.name, top_k=top_k)
    return {
        "site_id": site_id,
        "site_name": site.name,
        "similar_sites": similar,
    }
