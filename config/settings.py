"""Configuración centralizada via pydantic-settings + .env."""
from __future__ import annotations
from functools import lru_cache
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # PostgreSQL + pgvector (Supabase)
    database_url: str = "postgresql+asyncpg://postgres:password@db.project.supabase.co:5432/postgres"
    database_url_sync: str = "postgresql+psycopg2://postgres.project:password@aws-0-us-east-1.pooler.supabase.com:6543/postgres"

    # Similarity thresholds
    image_top_k: int = 5
    image_min_similarity: float = 0.60
    edge_min_similarity: float = 0.70  # umbral para crear arista en el grafo social

    # App
    env: str = "development"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _fix_db_driver(self) -> "Settings":
        """
        El host directo de Supabase (db.*.supabase.co:5432) resuelve solo a IPv6.
        El pooler (*.pooler.supabase.com:6543) tiene IPv4 y es el que funciona en redes
        que no soportan IPv6. Usamos DATABASE_URL_SYNC como base para la URL async,
        reemplazando el driver psycopg2 → asyncpg.
        """
        # Normalizar DATABASE_URL_SYNC a psycopg2 si viene sin driver
        sync = self.database_url_sync
        if sync.startswith("postgresql://") and "+psycopg2" not in sync:
            sync = sync.replace("postgresql://", "postgresql+psycopg2://", 1)
        self.database_url_sync = sync

        # Derivar la URL async desde el pooler (IPv4) en vez del host directo (IPv6)
        self.database_url = sync.replace("+psycopg2", "+asyncpg", 1)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
