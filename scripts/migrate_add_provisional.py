"""
Migración incremental: añade is_provisional a site_graph_edges y reclasifica
aristas existentes según el doble criterio de confiabilidad.

Ejecutar una sola vez contra la instancia Supabase:
    python scripts/migrate_add_provisional.py
"""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import psycopg2
from config.settings import settings


def main() -> None:
    url = settings.database_url_sync.replace("postgresql+psycopg2://", "postgresql://")
    print(f"Conectando a: {url.split('@')[1]}")

    conn = psycopg2.connect(url)
    conn.autocommit = True

    with conn.cursor() as cur:
        # 1. Añadir columna si no existe
        cur.execute("""
            ALTER TABLE site_graph_edges
            ADD COLUMN IF NOT EXISTS is_provisional BOOLEAN NOT NULL DEFAULT TRUE;
        """)
        print("Columna is_provisional asegurada.")

        # 2. Reclasificar aristas existentes según el doble criterio
        cur.execute(
            """
            UPDATE site_graph_edges
            SET is_provisional = NOT (
                weight >= %(reliable)s
                AND evidence_count >= %(min_ev)s
            );
            """,
            {
                "reliable": settings.edge_reliable_min_similarity,
                "min_ev": settings.edge_min_evidence,
            },
        )
        updated = cur.rowcount
        print(f"Aristas reclasificadas: {updated}")

        # 3. Reporte rápido
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE NOT is_provisional) AS reliable,
                COUNT(*) FILTER (WHERE is_provisional)     AS provisional,
                COUNT(*)                                   AS total
            FROM site_graph_edges;
        """)
        row = cur.fetchone()
        if row:
            reliable, provisional, total = row
            print(f"  Confiables : {reliable}/{total}")
            print(f"  Provisionales: {provisional}/{total}")

    conn.close()
    print("Migración completada.")


if __name__ == "__main__":
    main()
