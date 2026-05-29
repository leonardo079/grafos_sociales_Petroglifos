"""
Script de seed para image_embeddings.

Procesa imágenes de referencia, extrae embeddings EfficientNet-B0 (1280 dims)
y los inserta en Supabase para que el comparador encuentre similitudes iconográficas.

──────────────────────────────────────────────────────────────────────────────
MODO 1 — Estructura de carpetas:

    storage/reference_images/
        Geométrico/
            Piedras_del_Tunjo/
                tunjo_01.jpg
            Gámeza/
                gameza_03.jpg

    python -m scripts.seed_embeddings --folder storage/reference_images

MODO 2 — CSV manifest:

    image_path,site_name,municipality,taxonomy,reference_name
    storage/ref/img01.jpg,Piedras del Tunjo,Facatativá,Geométrico,Espiral central

    python -m scripts.seed_embeddings --csv storage/reference_images/manifest.csv

OPCIONES:
    --folder PATH       Carpeta con estructura taxonomy/sitio/imagen.ext
    --csv PATH          CSV con columnas requeridas
    --dry-run           Solo muestra cuántos embeddings se generarían
    --skip-existing     Omite imágenes ya en la BD (default: activado)
    --no-skip-existing  Fuerza reinserción
    --batch-size N      Insertar en lotes de N registros (default: 50)
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import structlog
from sqlalchemy import select, text

log = structlog.get_logger("seed_embeddings")

_SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
_TAXONOMY_NORMALIZE = {
    "geometrico": "Geométrico",
    "geométrico": "Geométrico",
    "astronomico": "Astronómico",
    "astronómico": "Astronómico",
    "hibrido": "Híbrido",
    "híbrido": "Híbrido",
    "antropomorfo": "Antropomorfo",
    "zoomorfo": "Zoomorfo",
    "fitomorfo": "Fitomorfo",
    "indeterminado": "Indeterminado",
}


def _normalize_taxonomy(raw: str) -> str:
    return _TAXONOMY_NORMALIZE.get(raw.strip().lower(), raw.strip().title())


def _collect_from_folder(folder: Path) -> list[dict]:
    """Recorre: folder/<taxonomy>/<site_name>/*.ext"""
    records = []
    for taxonomy_dir in sorted(folder.iterdir()):
        if not taxonomy_dir.is_dir():
            continue
        taxonomy = _normalize_taxonomy(taxonomy_dir.name)
        for site_dir in sorted(taxonomy_dir.iterdir()):
            if not site_dir.is_dir():
                continue
            site_name = site_dir.name.replace("_", " ")
            for img_file in sorted(site_dir.iterdir()):
                if img_file.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                    continue
                records.append({
                    "image_path": str(img_file),
                    "site_name": site_name,
                    "municipality": "",
                    "taxonomy": taxonomy,
                    "reference_name": img_file.stem.replace("_", " "),
                })
    return records


def _collect_from_csv(csv_path: Path) -> list[dict]:
    required = {"image_path", "site_name", "taxonomy"}
    records = []
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(f"El CSV debe tener: {required}. Encontradas: {reader.fieldnames}")
        for i, row in enumerate(reader, start=2):
            path = row["image_path"].strip()
            if not path:
                continue
            records.append({
                "image_path": path,
                "site_name": row.get("site_name", "").strip(),
                "municipality": row.get("municipality", "").strip(),
                "taxonomy": _normalize_taxonomy(row.get("taxonomy", "Indeterminado")),
                "reference_name": row.get("reference_name", "").strip(),
            })
    return records


def _extract_embeddings(records: list[dict]) -> tuple[list[dict], int]:
    from adapters.outbound.embeddings.efficientnet_adapter import extract_image_embedding

    results, errors = [], 0
    total = len(records)
    for i, rec in enumerate(records, start=1):
        img_path = rec["image_path"]
        if not os.path.exists(img_path):
            log.warning("image_not_found", path=img_path)
            errors += 1
            continue
        embedding = extract_image_embedding(img_path)
        if embedding is None:
            log.warning("embedding_failed", path=img_path)
            errors += 1
            continue
        results.append({**rec, "embedding": embedding})
        if i % 10 == 0 or i == total:
            log.info("embedding_progress", done=i, total=total, errors=errors)
    return results, errors


async def _get_existing_paths(session) -> set[str]:
    from infrastructure.database.models.models import ImageEmbedding
    result = await session.execute(select(ImageEmbedding.image_path))
    return {row[0] for row in result.fetchall()}


async def _insert_batch(session, batch: list[dict]) -> int:
    from infrastructure.database.models.models import ImageEmbedding
    for rec in batch:
        session.add(ImageEmbedding(
            site_name=rec["site_name"],
            municipality=rec["municipality"],
            reference_name=rec["reference_name"],
            taxonomy=rec["taxonomy"],
            image_path=rec["image_path"],
            embedding=rec["embedding"],
            metadata_={},
        ))
    await session.commit()
    return len(batch)


async def _run(
    records_with_embeddings: list[dict],
    batch_size: int,
    skip_existing: bool,
    dry_run: bool,
) -> None:
    from infrastructure.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        count_result = await session.execute(text("SELECT COUNT(*) FROM image_embeddings"))
        existing_count = count_result.scalar()

        existing_paths: set[str] = set()
        if skip_existing:
            existing_paths = await _get_existing_paths(session)
            log.info("skip_check", already_in_db=len(existing_paths))

        to_insert = [r for r in records_with_embeddings if r["image_path"] not in existing_paths]

        if not to_insert:
            log.info("nothing_to_insert")
            return

        if dry_run:
            log.info("dry_run", would_insert=len(to_insert), skipped=len(records_with_embeddings) - len(to_insert))
            for r in to_insert[:5]:
                log.info("dry_run_sample", site=r["site_name"], taxonomy=r["taxonomy"])
            return

        total_inserted = 0
        for i in range(0, len(to_insert), batch_size):
            batch = to_insert[i: i + batch_size]
            n = await _insert_batch(session, batch)
            total_inserted += n
            log.info("batch_inserted", batch=i // batch_size + 1, inserted=n, cumulative=total_inserted)

        log.info("seed_complete", inserted=total_inserted)

        # Crear índice IVFFlat si hay >= 100 filas
        new_count = existing_count + total_inserted
        if new_count >= 100:
            log.info("creating_ivfflat_index", rows=new_count)
            await session.execute(text("SET LOCAL maintenance_work_mem = '256MB'"))
            await session.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_img_embeddings_embedding
                ON image_embeddings
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 50)
            """))
            await session.commit()
            log.info("ivfflat_index_ready")
        else:
            log.info("ivfflat_index_skipped", reason=f"need >=100 rows, have {new_count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed de image_embeddings para el módulo de grafos sociales",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--folder", metavar="PATH")
    source.add_argument("--csv", metavar="PATH")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-existing", action="store_true", default=True)
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    parser.add_argument("--batch-size", type=int, default=50, metavar="N")
    args = parser.parse_args()

    if args.folder:
        folder = Path(args.folder)
        if not folder.is_dir():
            log.error("folder_not_found", path=str(folder))
            sys.exit(1)
        records = _collect_from_folder(folder)
    else:
        csv_path = Path(args.csv)
        if not csv_path.is_file():
            log.error("csv_not_found", path=str(csv_path))
            sys.exit(1)
        records = _collect_from_csv(csv_path)

    log.info("records_collected", total=len(records))
    if not records:
        log.warning("no_records_found")
        sys.exit(0)

    log.info("loading_efficientnet_b0")
    records_with_embeddings, errors = _extract_embeddings(records)
    log.info("embeddings_extracted", success=len(records_with_embeddings), errors=errors)

    if not records_with_embeddings:
        log.error("no_embeddings_generated")
        sys.exit(1)

    asyncio.run(_run(
        records_with_embeddings=records_with_embeddings,
        batch_size=args.batch_size,
        skip_existing=args.skip_existing,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
