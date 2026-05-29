"""Crea las tablas del módulo en Supabase leyendo DATABASE_URL_SYNC del .env."""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import psycopg2
from config.settings import settings

SQL = (Path(__file__).parent.parent / "infrastructure/database/migrations/schema.sql").read_text()

def main() -> None:
    url = settings.database_url_sync
    # psycopg2 no acepta el prefijo +psycopg2
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    print(f"Conectando a: {url.split('@')[1]}")  # solo host, sin credenciales
    conn = psycopg2.connect(url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(SQL)
    conn.close()
    print("Tablas creadas correctamente.")

if __name__ == "__main__":
    main()
