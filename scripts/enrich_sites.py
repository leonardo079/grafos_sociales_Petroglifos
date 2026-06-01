"""
Enriquece rupestrian_sites con los campos que el pipeline de comparación no rellena:
  - department          → del catálogo de sitios andinos (build_manifest.SITIOS_ANDINOS)
  - dominant_taxonomy   → taxonomía más frecuente entre las imágenes del sitio (image_embeddings)
  - petroglyph_count    → número de imágenes del corpus asignadas al sitio

El pipeline (compare_image → _get_or_create_site) solo guarda name + municipality,
por eso estos tres campos quedan vacíos/por defecto. Este script los completa en bloque.

Uso:
    python -m scripts.enrich_sites
"""
from __future__ import annotations
import asyncio
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from sqlalchemy import select
from infrastructure.database.session import AsyncSessionLocal
from infrastructure.database.models.models import RupestranSiteModel, ImageEmbedding
from scripts.build_manifest import SITIOS_ANDINOS


# Mapeos rápidos site_name → department / municipality desde el catálogo
_DEPT_BY_SITE = {s["site_name"]: s["department"] for s in SITIOS_ANDINOS}
_MUNI_BY_SITE = {s["site_name"]: s["municipality"] for s in SITIOS_ANDINOS}


async def main() -> None:
    async with AsyncSessionLocal() as session:
        # Agregar estadísticas del corpus por nombre de sitio
        embeddings = list((await session.execute(select(ImageEmbedding))).scalars().all())
        tax_by_site: dict[str, Counter] = {}
        count_by_site: dict[str, int] = {}
        for e in embeddings:
            tax_by_site.setdefault(e.site_name, Counter())[e.taxonomy] += 1
            count_by_site[e.site_name] = count_by_site.get(e.site_name, 0) + 1

        sites = list((await session.execute(select(RupestranSiteModel))).scalars().all())
        updated = 0
        for site in sites:
            counter = tax_by_site.get(site.name)
            dominant = counter.most_common(1)[0][0] if counter else "Indeterminado"
            count = count_by_site.get(site.name, 0)
            dept = _DEPT_BY_SITE.get(site.name, site.department)
            muni = _MUNI_BY_SITE.get(site.name, site.municipality)

            site.dominant_taxonomy = dominant
            site.petroglyph_count = count
            if dept:
                site.department = dept
            if muni and not site.municipality:
                site.municipality = muni
            updated += 1
            print(f"{site.name:22} | {dept:13} | {dominant:14} | {count} petroglifos")

        await session.commit()
        print(f"\n{updated} sitios enriquecidos.")


if __name__ == "__main__":
    asyncio.run(main())
