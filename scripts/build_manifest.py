"""
Genera un CSV manifest para seed_embeddings y bulk_compare a partir de
storage/reference_images/<clase>/<imagen>.jpg

El dataset de Roboflow solo está clasificado por TIPO de petroglifo (taxonomía),
no por sitio. Como el grafo conecta SITIOS, aquí asignamos a cada imagen un sitio
arqueológico REAL de la región andina colombiana, ELEGIDO AL AZAR (semilla fija
para reproducibilidad). La asignación a sitios es sintética; la taxonomía es real.

Uso:
    python -m scripts.build_manifest
    python -m scripts.build_manifest --seed 42 --out storage/reference_images/manifest.csv
"""
from __future__ import annotations
import argparse
import csv
import random
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_IMAGES_DIR = _ROOT / "storage" / "reference_images"
_SUPPORTED = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

# Sitios reales con arte rupestre en la región andina (altiplano cundiboyacense)
SITIOS_ANDINOS = [
    {"site_name": "Villa de Leyva",    "municipality": "Villa de Leyva", "department": "Boyacá"},
    {"site_name": "Sáchica",           "municipality": "Sáchica",        "department": "Boyacá"},
    {"site_name": "Gámeza",            "municipality": "Gámeza",         "department": "Boyacá"},
    {"site_name": "Sogamoso",          "municipality": "Sogamoso",       "department": "Boyacá"},
    {"site_name": "Tunja",             "municipality": "Tunja",          "department": "Boyacá"},
    {"site_name": "Piedras del Tunjo", "municipality": "Facatativá",     "department": "Cundinamarca"},
    {"site_name": "Sutatausa",         "municipality": "Sutatausa",      "department": "Cundinamarca"},
    {"site_name": "Tibacuy",           "municipality": "Tibacuy",        "department": "Cundinamarca"},
    {"site_name": "Soacha",            "municipality": "Soacha",         "department": "Cundinamarca"},
    {"site_name": "Zipaquirá",         "municipality": "Zipaquirá",      "department": "Cundinamarca"},
    {"site_name": "El Colegio",        "municipality": "El Colegio",     "department": "Cundinamarca"},
    {"site_name": "Pandi",             "municipality": "Pandi",          "department": "Cundinamarca"},
]

# Normalización del nombre de carpeta → taxonomía oficial
_TAX = {
    "antropomorfo": "Antropomorfo",
    "geometrico": "Geométrico",
    "geométrico": "Geométrico",
    "zoomorfo": "Zoomorfo",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera el CSV manifest con sitios andinos aleatorios")
    parser.add_argument("--seed", type=int, default=42, help="Semilla aleatoria (reproducibilidad)")
    parser.add_argument("--out", default=str(_IMAGES_DIR / "manifest.csv"), help="Ruta del CSV de salida")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    rows: list[dict] = []
    counts_by_tax: dict[str, int] = {}
    counts_by_site: dict[str, int] = {}

    for class_dir in sorted(_IMAGES_DIR.iterdir()):
        if not class_dir.is_dir():
            continue
        taxonomy = _TAX.get(class_dir.name.strip().lower(), class_dir.name.strip().title())
        for img in sorted(class_dir.iterdir()):
            if img.suffix.lower() not in _SUPPORTED:
                continue
            sitio = rng.choice(SITIOS_ANDINOS)
            # reference_name: nombre corto y legible a partir del archivo
            ref = img.stem.split("_png")[0].split(".rf.")[0]
            rows.append({
                "image_path": str(img.relative_to(_ROOT)),
                "site_name": sitio["site_name"],
                "municipality": sitio["municipality"],
                "department": sitio["department"],
                "taxonomy": taxonomy,
                "reference_name": f"{taxonomy} — {ref}",
            })
            counts_by_tax[taxonomy] = counts_by_tax.get(taxonomy, 0) + 1
            counts_by_site[sitio["site_name"]] = counts_by_site.get(sitio["site_name"], 0) + 1

    if not rows:
        print("No se encontraron imágenes en", _IMAGES_DIR)
        sys.exit(1)

    out_path = Path(args.out)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["image_path", "site_name", "municipality", "department", "taxonomy", "reference_name"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Manifest generado: {out_path}  ({len(rows)} imágenes)")
    print("\nPor taxonomía:")
    for t, n in sorted(counts_by_tax.items()):
        print(f"  {t}: {n}")
    print("\nPor sitio (asignación aleatoria):")
    for s, n in sorted(counts_by_site.items(), key=lambda x: -x[1]):
        print(f"  {s}: {n}")


if __name__ == "__main__":
    main()
