"""
Compara todas las imágenes del manifest contra el corpus en pgvector
y construye el grafo social completo en la BD.

Uso:
    python -m scripts.bulk_compare --csv storage/reference_images/manifest_large.csv
    python -m scripts.bulk_compare --csv storage/reference_images/manifest.csv
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import structlog
from infrastructure.database.session import AsyncSessionLocal
from graphs.social_graph import PetroglyphSocialGraph
from orchestrator.comparator import compare_image

log = structlog.get_logger("bulk_compare")


async def run(csv_path: Path) -> None:
    records = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    total = len(records)
    log.info("bulk_compare_start", total=total)

    graph = PetroglyphSocialGraph()
    edges_total = 0
    errors = 0

    for i, row in enumerate(records, start=1):
        image_path   = row["image_path"].strip()
        site         = row.get("site_name", "").strip()
        municipality = row.get("municipality", "").strip()

        if not Path(image_path).exists():
            log.warning("image_not_found", path=image_path)
            errors += 1
            continue

        # Sesión independiente por imagen: un fallo no afecta las demás
        async with AsyncSessionLocal() as session:
            try:
                result = await compare_image(
                    image_path=image_path,
                    site=site,
                    municipality=municipality,
                    department=row.get("department", "").strip(),
                    session=session,
                    graph=graph,
                )
                await session.commit()
                edges_total += result.get("edges_persisted", 0)

                if i % 10 == 0 or i == total:
                    log.info(
                        "progress",
                        done=i,
                        total=total,
                        edges_so_far=edges_total,
                        matches=len(result.get("matches", [])),
                    )

            except Exception as exc:
                await session.rollback()
                log.error("compare_error", path=image_path, error=str(exc))
                errors += 1

    log.info(
        "bulk_compare_done",
        processed=total - errors,
        errors=errors,
        edges_persisted=edges_total,
        graph_nodes=graph._G.number_of_nodes(),
        graph_edges=graph._G.number_of_edges(),
    )
    print(f"\nGrafo resultante: {graph._G.number_of_nodes()} nodos, {graph._G.number_of_edges()} aristas")
    print(f"Aristas persistidas en BD: {edges_total}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compara todas las imágenes del manifest y construye el grafo social")
    parser.add_argument("--csv", required=True, metavar="PATH", help="CSV manifest (image_path, site_name, municipality, taxonomy)")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        print(f"CSV no encontrado: {csv_path}")
        sys.exit(1)

    asyncio.run(run(csv_path))


if __name__ == "__main__":
    main()
