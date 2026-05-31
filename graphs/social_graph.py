"""
Grafo social de similitud iconográfica entre sitios rupestres.

Nodos  = sitios arqueológicos (Villa de Leyva, Gámeza, Facatativá…)
Aristas = similitud coseno entre motivos detectados (peso 0–1)

Análisis disponibles:
- Comunidades (Louvain)
- PageRank (sitios más "centrales" en la red rupestre)
- Centralidad de intermediación
- Métricas de topología (clustering, components, diameter, degree distribution)
- Exportación HTML interactiva con PyVis
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
import structlog
import networkx as nx

log = structlog.get_logger(__name__)

GRAPH_OUTPUT_DIR = Path("storage/graphs")


GRAPH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _compute_confidence_level(weight: float, evidence_count: int) -> str:
    """Clasifica una arista en low/medium/high según similitud y cantidad de evidencias."""
    from config.settings import settings
    if weight >= 0.85 and evidence_count >= 3:
        return "high"
    if weight >= settings.edge_reliable_min_similarity and evidence_count >= settings.edge_min_evidence:
        return "medium"
    return "low"


class PetroglyphSocialGraph:
    """
    Grafo ponderado no dirigido de similitud iconográfica entre sitios rupestres.

    Uso típico (desde orchestrator/comparator.py):
        graph = PetroglyphSocialGraph()
        graph.add_site("Villa de Leyva", municipality="Villa de Leyva", department="Boyacá")
        graph.add_or_update_edge("Villa de Leyva", "Gámeza", weight=0.83, taxonomy="Geométrico")
        graph.export_html("storage/graphs/red_rupestre.html")
    """

    def __init__(self) -> None:
        self._G: nx.Graph = nx.Graph()

    # ── Construcción del grafo ────────────────────────────────────────────────

    def add_site(
        self,
        site_id: str,
        *,
        municipality: str = "",
        department: str = "",
        dominant_taxonomy: str = "Indeterminado",
        petroglyph_count: int = 0,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> None:
        """Agrega o actualiza un nodo (sitio rupestre)."""
        self._G.add_node(
            site_id,
            municipality=municipality,
            department=department,
            dominant_taxonomy=dominant_taxonomy,
            petroglyph_count=petroglyph_count,
            latitude=latitude,
            longitude=longitude,
        )

    def add_or_update_edge(
        self,
        site_a: str,
        site_b: str,
        weight: float,
        taxonomy: str = "",
    ) -> None:
        """Agrega o actualiza una arista. Si existe, promedia el peso y acumula evidencia."""
        from config.settings import settings

        if site_a == site_b:
            return
        for s in (site_a, site_b):
            if s not in self._G:
                self.add_site(s)

        if self._G.has_edge(site_a, site_b):
            data = self._G[site_a][site_b]
            n = data.get("evidence_count", 1)
            data["weight"] = round((data["weight"] * n + weight) / (n + 1), 4)
            data["evidence_count"] = n + 1
            if taxonomy and taxonomy not in data.get("shared_taxonomies", []):
                data.setdefault("shared_taxonomies", []).append(taxonomy)
        else:
            self._G.add_edge(
                site_a,
                site_b,
                weight=round(weight, 4),
                evidence_count=1,
                shared_taxonomies=[taxonomy] if taxonomy else [],
            )
            data = self._G[site_a][site_b]

        data["is_provisional"] = not (
            data["weight"] >= settings.edge_reliable_min_similarity
            and data["evidence_count"] >= settings.edge_min_evidence
        )
        log.debug("graph_edge_updated", site_a=site_a, site_b=site_b, weight=weight)

    def load_persisted_edge(
        self,
        site_a: str,
        site_b: str,
        *,
        weight: float,
        evidence_count: int,
        shared_taxonomies: list[str] | None = None,
        is_provisional: bool | None = None,
    ) -> None:
        """
        Carga una arista con su estado ya persistido (sin tratarla como una
        observación nueva). Usado al reconstruir el grafo desde la BD: preserva
        weight y evidence_count tal cual están guardados.

        Si is_provisional viene None, se recalcula desde los umbrales de settings.
        """
        from config.settings import settings

        if site_a == site_b:
            return
        for s in (site_a, site_b):
            if s not in self._G:
                self.add_site(s)

        if is_provisional is None:
            is_provisional = not (
                weight >= settings.edge_reliable_min_similarity
                and evidence_count >= settings.edge_min_evidence
            )

        self._G.add_edge(
            site_a,
            site_b,
            weight=round(weight, 4),
            evidence_count=evidence_count,
            shared_taxonomies=list(shared_taxonomies or []),
            is_provisional=is_provisional,
        )

    # ── Análisis ──────────────────────────────────────────────────────────────

    def _reliable_subgraph(self) -> nx.Graph:
        """Subgrafo con solo aristas confiables (is_provisional=False)."""
        reliable = [
            (u, v) for u, v, d in self._G.edges(data=True)
            if not d.get("is_provisional", True)
        ]
        return self._G.edge_subgraph(reliable)

    def pagerank(self, alpha: float = 0.85) -> dict[str, float]:
        """PageRank usando solo aristas confiables — mayor estabilidad entre corridas."""
        G = self._reliable_subgraph()
        if len(G) == 0:
            return {}
        return nx.pagerank(G, alpha=alpha, weight="weight")

    def communities(self) -> list[set[str]]:
        """Comunidades Louvain sobre aristas confiables (greedy modularity como fallback)."""
        G = self._reliable_subgraph()
        if len(G) == 0:
            return []
        try:
            from community import best_partition  # type: ignore
            partition = best_partition(G, weight="weight")
            groups: dict[int, set[str]] = {}
            for node, comm_id in partition.items():
                groups.setdefault(comm_id, set()).add(node)
            return list(groups.values())
        except ImportError:
            comms = nx.algorithms.community.greedy_modularity_communities(G, weight="weight")
            return [set(c) for c in comms]

    def betweenness_centrality(self) -> dict[str, float]:
        """Centralidad de intermediación sobre aristas confiables."""
        G = self._reliable_subgraph()
        if len(G) == 0:
            return {}
        return nx.betweenness_centrality(G, weight="weight", normalized=True)

    def most_similar_sites(self, site_id: str, top_k: int = 5) -> list[dict]:
        """Top-k sitios más similares a uno dado, ordenados por peso de arista."""
        if site_id not in self._G:
            return []
        neighbors = [
            {
                "site": nb,
                "weight": data["weight"],
                "evidence_count": data.get("evidence_count", 1),
                "shared_taxonomies": data.get("shared_taxonomies", []),
                "is_provisional": data.get("is_provisional", True),
                "confidence_level": _compute_confidence_level(
                    data["weight"], data.get("evidence_count", 1)
                ),
            }
            for nb, data in self._G[site_id].items()
        ]
        return sorted(neighbors, key=lambda x: x["weight"], reverse=True)[:top_k]

    def metrics(self) -> dict:
        """
        Métricas de topología del grafo.

        Incluye: clustering coefficient, componentes conectados,
        distribución de grado (top hubs) y diámetro de la red.
        """
        n = self._G.number_of_nodes()
        e = self._G.number_of_edges()
        if n == 0:
            return {"nodes": 0, "edges": 0}

        # Clustering coefficient (transitividad iconográfica)
        clustering = round(nx.average_clustering(self._G, weight="weight"), 4)

        # Componentes conectados
        components = list(nx.connected_components(self._G))
        components_sorted = sorted(components, key=len, reverse=True)
        num_components = len(components)
        largest_size = len(components_sorted[0]) if components_sorted else 0

        # Diámetro sobre el componente más grande (evita error en grafo desconectado)
        diameter = None
        if largest_size > 1:
            largest_subgraph = self._G.subgraph(components_sorted[0])
            try:
                diameter = nx.diameter(largest_subgraph)
            except nx.NetworkXError:
                diameter = None

        # Distribución de grado
        degrees = dict(self._G.degree())
        avg_degree = round(sum(degrees.values()) / n, 2) if n > 0 else 0.0
        top_hubs = sorted(
            [{"site": s, "degree": d} for s, d in degrees.items()],
            key=lambda x: x["degree"],
            reverse=True,
        )[:5]

        weights = [d["weight"] for _, _, d in self._G.edges(data=True)]
        avg_similarity = round(sum(weights) / len(weights), 4) if weights else 0.0

        return {
            "nodes": n,
            "edges": e,
            "density": round(nx.density(self._G), 4),
            "avg_similarity": avg_similarity,
            "clustering_coefficient": clustering,
            "connected_components": num_components,
            "largest_component_size": largest_size,
            "diameter": diameter,
            "degree_distribution": {
                "avg_degree": avg_degree,
                "top_hubs": top_hubs,
            },
        }

    def summary(self) -> dict:
        """Resumen estadístico básico del grafo (compatible con to_dict)."""
        if len(self._G) == 0:
            return {"nodes": 0, "edges": 0}
        pr = self.pagerank()
        top_site = max(pr, key=pr.get) if pr else ""
        weights = [d["weight"] for _, _, d in self._G.edges(data=True)]
        return {
            "nodes": self._G.number_of_nodes(),
            "edges": self._G.number_of_edges(),
            "avg_similarity": round(sum(weights) / len(weights), 4) if weights else 0.0,
            "max_similarity": round(max(weights), 4) if weights else 0.0,
            "most_central_site": top_site,
            "communities": len(self.communities()),
            "density": round(nx.density(self._G), 4),
        }

    # ── Serialización ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serializa el grafo a dict para la API."""
        return {
            "nodes": [{"id": n, **self._G.nodes[n]} for n in self._G.nodes],
            "edges": [
                {
                    "source": u,
                    "target": v,
                    **d,
                    "confidence_level": _compute_confidence_level(
                        d.get("weight", 0.0), d.get("evidence_count", 1)
                    ),
                }
                for u, v, d in self._G.edges(data=True)
            ],
            "summary": self.summary(),
            "generated_at": datetime.utcnow().isoformat(),
        }

    def save_json(self, path: str | None = None) -> str:
        out = Path(path) if path else GRAPH_OUTPUT_DIR / "social_graph.json"
        out.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("graph_saved_json", path=str(out))
        return str(out)

    def export_html(self, path: str | None = None, height: str = "750px") -> str:
        """Exporta visualización interactiva con PyVis."""
        try:
            from pyvis.network import Network
        except ImportError:
            log.error("pyvis_not_installed")
            return ""

        out = Path(path) if path else GRAPH_OUTPUT_DIR / "red_rupestre.html"
        net = Network(
            height=height, width="100%", bgcolor="#1a1a2e", font_color="white",
            notebook=False, directed=False,
            cdn_resources="in_line",  # embebe vis-network en el HTML (evita 404 de lib/ al servir vía API)
        )
        net.set_options("""
        {
          "physics": {"solver": "forceAtlas2Based", "stabilization": {"iterations": 150}},
          "edges": {"smooth": {"type": "continuous"}, "color": {"inherit": "both"}},
          "nodes": {"shape": "dot", "scaling": {"min": 10, "max": 40}},
          "interaction": {"hover": true, "tooltipDelay": 200}
        }
        """)

        pr = self.pagerank()
        communities_list = self.communities()
        node_community: dict[str, int] = {}
        for i, comm in enumerate(communities_list):
            for node in comm:
                node_community[node] = i

        COLORS = [
            "#e74c3c", "#3498db", "#2ecc71", "#f39c12",
            "#9b59b6", "#1abc9c", "#e67e22", "#34495e",
        ]

        for node in self._G.nodes:
            attrs = self._G.nodes[node]
            size = 15 + int(pr.get(node, 0) * 500)
            color = COLORS[node_community.get(node, 0) % len(COLORS)]
            title = (
                f"<b>{node}</b><br>"
                f"Municipio: {attrs.get('municipality', '')}<br>"
                f"Taxonomía dominante: {attrs.get('dominant_taxonomy', 'Indeterminado')}<br>"
                f"Petroglifos: {attrs.get('petroglyph_count', 0)}<br>"
                f"PageRank: {pr.get(node, 0):.4f}"
            )
            net.add_node(node, label=node, size=size, color=color, title=title)

        for u, v, data in self._G.edges(data=True):
            weight = data.get("weight", 0.5)
            title = (
                f"Similitud: {weight:.2%}<br>"
                f"Evidencias: {data.get('evidence_count', 1)}<br>"
                f"Taxonomías compartidas: {', '.join(data.get('shared_taxonomies', [])) or 'N/A'}"
            )
            net.add_edge(u, v, value=weight, title=title, width=weight * 5)

        net.save_graph(str(out))

        # PyVis no incluye <!DOCTYPE html>, lo que activa Quirks Mode en el navegador.
        # Lo anteponemos para forzar Standards Mode.
        html = out.read_text(encoding="utf-8")
        if not html.lstrip().lower().startswith("<!doctype"):
            out.write_text("<!DOCTYPE html>\n" + html, encoding="utf-8")

        log.info("graph_exported_html", path=str(out), nodes=len(self._G.nodes))
        return str(out)

    def export_plotly(self, path: str | None = None) -> str:
        """
        Exporta el grafo como HTML interactivo con Plotly.
        Más claro que PyVis: etiquetas visibles, colores por comunidad,
        grosor de arista proporcional a similitud.
        """
        import plotly.graph_objects as go

        if len(self._G) == 0:
            return ""

        pos = nx.spring_layout(self._G, weight="weight", seed=42, k=2.5)
        pr = self.pagerank()
        communities_list = self.communities()
        node_community: dict[str, int] = {}
        for i, comm in enumerate(communities_list):
            for node in comm:
                node_community[node] = i

        PALETTE = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c"]

        # ── Aristas ───────────────────────────────────────────────────────────
        edge_traces = []
        for u, v, data in self._G.edges(data=True):
            x0, y0 = pos[u]
            x1, y1 = pos[v]
            weight = data.get("weight", 0.5)
            taxonomies = ", ".join(data.get("shared_taxonomies", [])) or "N/A"
            edge_traces.append(go.Scatter(
                x=[x0, x1, None], y=[y0, y1, None],
                mode="lines",
                line=dict(width=weight * 8, color="rgba(100,100,100,0.5)"),
                hoverinfo="text",
                text=f"<b>{u} ↔ {v}</b><br>Similitud: {weight:.2%}<br>Evidencias: {data.get('evidence_count', 1)}<br>Taxonomías: {taxonomies}",
                showlegend=False,
            ))

        # ── Etiquetas en el punto medio de cada arista ────────────────────────
        edge_label_traces = []
        for u, v, data in self._G.edges(data=True):
            x0, y0 = pos[u]
            x1, y1 = pos[v]
            weight = data.get("weight", 0.5)
            edge_label_traces.append(go.Scatter(
                x=[(x0 + x1) / 2], y=[(y0 + y1) / 2],
                mode="text",
                text=[f"{weight:.2%}"],
                textfont=dict(size=10, color="#555"),
                hoverinfo="skip",
                showlegend=False,
            ))

        # ── Nodos ─────────────────────────────────────────────────────────────
        node_traces = []
        for comm_id in sorted(set(node_community.values())):
            nodes_in_comm = [n for n in self._G.nodes if node_community.get(n) == comm_id]
            xs = [pos[n][0] for n in nodes_in_comm]
            ys = [pos[n][1] for n in nodes_in_comm]
            sizes = [20 + pr.get(n, 0) * 600 for n in nodes_in_comm]
            attrs_list = [self._G.nodes[n] for n in nodes_in_comm]
            hover_texts = [
                f"<b>{n}</b><br>"
                f"Municipio: {a.get('municipality', '')}<br>"
                f"Taxonomía: {a.get('dominant_taxonomy', 'Indeterminado')}<br>"
                f"PageRank: {pr.get(n, 0):.4f}<br>"
                f"Comunidad: {comm_id + 1}"
                for n, a in zip(nodes_in_comm, attrs_list)
            ]
            color = PALETTE[comm_id % len(PALETTE)]
            node_traces.append(go.Scatter(
                x=xs, y=ys,
                mode="markers+text",
                marker=dict(size=sizes, color=color, line=dict(width=2, color="white")),
                text=nodes_in_comm,
                textposition="top center",
                textfont=dict(size=13, color="#222"),
                hovertext=hover_texts,
                hoverinfo="text",
                name=f"Comunidad {comm_id + 1}",
            ))

        fig = go.Figure(
            data=edge_traces + edge_label_traces + node_traces,
            layout=go.Layout(
                title=dict(
                    text="Red Social de Similitud Iconográfica — Sitios Rupestres",
                    font=dict(size=18),
                    x=0.5,
                ),
                showlegend=True,
                hovermode="closest",
                plot_bgcolor="white",
                paper_bgcolor="white",
                xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                margin=dict(l=20, r=20, t=60, b=20),
                height=650,
                legend=dict(title="Comunidades", bordercolor="#ccc", borderwidth=1),
                annotations=[
                    dict(
                        text=f"Nodos: {self._G.number_of_nodes()} | Aristas: {self._G.number_of_edges()} | Densidad: {nx.density(self._G):.2f}",
                        xref="paper", yref="paper", x=0.01, y=0.01,
                        showarrow=False, font=dict(size=11, color="#888"),
                    )
                ],
            ),
        )

        out = Path(path) if path else GRAPH_OUTPUT_DIR / "red_rupestre_plotly.html"
        fig.write_html(str(out), include_plotlyjs="cdn")
        log.info("graph_exported_plotly", path=str(out), nodes=len(self._G.nodes))
        return str(out)

    # ── Persistencia en PostgreSQL ────────────────────────────────────────────

    async def sync_to_db(self, session) -> None:
        """Sincroniza todas las aristas del grafo en memoria a site_graph_edges."""
        from infrastructure.database.models.models import SiteGraphEdge, RupestranSiteModel
        from sqlalchemy import select

        result = await session.execute(select(RupestranSiteModel))
        sites_by_name = {s.name: s.id for s in result.scalars()}

        for u, v, data in self._G.edges(data=True):
            id_a = sites_by_name.get(u)
            id_b = sites_by_name.get(v)
            if not id_a or not id_b:
                continue
            existing = (await session.execute(
                select(SiteGraphEdge).where(
                    SiteGraphEdge.site_a_id == id_a,
                    SiteGraphEdge.site_b_id == id_b,
                )
            )).scalar_one_or_none()
            if existing:
                existing.weight = data["weight"]
                existing.evidence_count = data.get("evidence_count", 1)
                existing.shared_taxonomies = data.get("shared_taxonomies", [])
                existing.is_provisional = data.get("is_provisional", True)
            else:
                session.add(SiteGraphEdge(
                    site_a_id=id_a,
                    site_b_id=id_b,
                    weight=data["weight"],
                    evidence_count=data.get("evidence_count", 1),
                    shared_taxonomies=data.get("shared_taxonomies", []),
                    is_provisional=data.get("is_provisional", True),
                ))
        await session.commit()
        log.info("graph_synced_to_db", edges=self._G.number_of_edges())
