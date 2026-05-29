"""
Genera imágenes sintéticas de petroglifos y un CSV manifest para poblar
image_embeddings con un corpus más representativo.

Cada taxonomía tiene formas visuales distintas para que EfficientNet-B0
produzca embeddings diferenciados y las similitudes sean significativas.

Uso:
    python -m scripts.generate_synthetic_corpus
"""
from __future__ import annotations
import csv
import math
import random
from pathlib import Path
from PIL import Image, ImageDraw

random.seed(42)

OUTPUT_DIR = Path("storage/reference_images/synthetic")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MANIFEST_PATH = Path("storage/reference_images/manifest_large.csv")

# ── Sitios reales de la región andina colombiana ──────────────────────────────
SITES = [
    # Boyacá
    ("Gámeza",               "Gámeza",           "Boyacá"),
    ("Villa de Leyva",       "Villa de Leyva",    "Boyacá"),
    ("Chivor",               "Chivor",            "Boyacá"),
    ("Sogamoso",             "Sogamoso",          "Boyacá"),
    ("Soatá",                "Soatá",             "Boyacá"),
    ("Monguí",               "Monguí",            "Boyacá"),
    ("Tunja",                "Tunja",             "Boyacá"),
    ("Chiquinquirá",         "Chiquinquirá",      "Boyacá"),
    ("Ráquira",              "Ráquira",           "Boyacá"),
    ("Tenza",                "Tenza",             "Boyacá"),
    # Cundinamarca
    ("Piedras del Tunjo",    "Facatativá",        "Cundinamarca"),
    ("Supatá",               "Supatá",            "Cundinamarca"),
    ("Zipaquirá",            "Zipaquirá",         "Cundinamarca"),
    ("Nemocón",              "Nemocón",           "Cundinamarca"),
    ("La Mesa",              "La Mesa",           "Cundinamarca"),
    ("Tibacuy",              "Tibacuy",           "Cundinamarca"),
    ("Bojacá",               "Bojacá",            "Cundinamarca"),
    # Santander
    ("Guane",                "Barichara",         "Santander"),
    ("Jordán",               "San Gil",           "Santander"),
    ("Cepitá",               "Cepitá",            "Santander"),
    ("Charalá",              "Charalá",           "Santander"),
    # Huila
    ("San Agustín",          "San Agustín",       "Huila"),
    ("Isnos",                "Isnos",             "Huila"),
    ("La Plata",             "La Plata",          "Huila"),
    # Nariño
    ("La Florida",           "La Florida",        "Nariño"),
    ("Cumbal",               "Cumbal",            "Nariño"),
    ("Ipiales",              "Ipiales",           "Nariño"),
    # Antioquia
    ("Santa Fe de Antioquia","Santa Fe de Antioquia","Antioquia"),
    ("Sopetrán",             "Sopetrán",          "Antioquia"),
    ("Amalfi",               "Amalfi",            "Antioquia"),
]

TAXONOMIES = ["Antropomorfo", "Zoomorfo", "Geométrico"]

IMG_SIZE = 224  # EfficientNet espera 224×224
N_PER_SITE_TAX = 2  # imágenes por combinación sitio+taxonomía seleccionada


# ── Generadores de imágenes por taxonomía ────────────────────────────────────

def _bg(draw: ImageDraw.ImageDraw, color: tuple) -> None:
    draw.rectangle([0, 0, IMG_SIZE, IMG_SIZE], fill=color)


def make_antropomorfo(idx: int) -> Image.Image:
    """Figura humana estilizada: cabeza, torso, extremidades."""
    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (210, 180, 140))
    draw = ImageDraw.Draw(img)
    r = random.Random(idx)
    cx, cy = IMG_SIZE // 2, IMG_SIZE // 2
    scale = r.uniform(0.6, 0.9)
    s = int(40 * scale)

    # cabeza
    draw.ellipse([cx - s, cy - 3*s, cx + s, cy - s], outline=(60, 30, 10), width=4)
    # torso
    draw.line([cx, cy - s, cx, cy + s], fill=(60, 30, 10), width=5)
    # brazos
    angle = r.uniform(-30, 30)
    arm_len = int(1.5 * s)
    ax = int(arm_len * math.cos(math.radians(angle + 90)))
    ay = int(arm_len * math.sin(math.radians(angle + 90)))
    draw.line([cx - ax, cy - ay + s // 2, cx + ax, cy + ay + s // 2], fill=(60, 30, 10), width=4)
    # piernas
    spread = r.randint(s // 2, s)
    draw.line([cx, cy + s, cx - spread, cy + 3*s], fill=(60, 30, 10), width=4)
    draw.line([cx, cy + s, cx + spread, cy + 3*s], fill=(60, 30, 10), width=4)
    # ruido de textura
    for _ in range(300):
        x, y = r.randint(0, IMG_SIZE - 1), r.randint(0, IMG_SIZE - 1)
        draw.point((x, y), fill=(r.randint(150, 200), r.randint(120, 170), r.randint(80, 130)))
    return img


def make_zoomorfo(idx: int) -> Image.Image:
    """Animal estilizado: cuerpo ovalado, patas, cola."""
    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (180, 200, 160))
    draw = ImageDraw.Draw(img)
    r = random.Random(idx + 1000)
    cx, cy = IMG_SIZE // 2, IMG_SIZE // 2
    w = r.randint(50, 80)
    h = r.randint(30, 50)

    # cuerpo
    draw.ellipse([cx - w, cy - h, cx + w, cy + h], outline=(40, 60, 30), width=4)
    # cabeza
    hx = cx + w + r.randint(10, 25)
    draw.ellipse([hx - 20, cy - 20, hx + 20, cy + 20], outline=(40, 60, 30), width=4)
    # cola
    tx = cx - w - r.randint(10, 30)
    draw.arc([tx - 30, cy - 30, tx + 10, cy + 30], start=200, end=340, fill=(40, 60, 30), width=3)
    # patas
    n_legs = r.choice([4, 6])
    for i in range(n_legs):
        lx = cx - w + i * (2 * w) // n_legs
        draw.line([lx, cy + h, lx + r.randint(-10, 10), cy + h + r.randint(20, 35)],
                  fill=(40, 60, 30), width=3)
    # textura
    for _ in range(300):
        x, y = r.randint(0, IMG_SIZE - 1), r.randint(0, IMG_SIZE - 1)
        draw.point((x, y), fill=(r.randint(130, 190), r.randint(150, 210), r.randint(110, 170)))
    return img


def make_geometrico(idx: int) -> Image.Image:
    """Espirales, círculos concéntricos y líneas geométricas."""
    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (200, 190, 210))
    draw = ImageDraw.Draw(img)
    r = random.Random(idx + 2000)
    cx, cy = IMG_SIZE // 2, IMG_SIZE // 2
    motif = r.choice(["spirals", "concentric", "grid"])

    if motif == "spirals":
        for ring in range(3, 8):
            rad = ring * 14
            draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad],
                         outline=(80, 50, 100), width=3)
        # líneas radiales
        for angle in range(0, 360, r.randint(30, 60)):
            ex = cx + int(100 * math.cos(math.radians(angle)))
            ey = cy + int(100 * math.sin(math.radians(angle)))
            draw.line([cx, cy, ex, ey], fill=(80, 50, 100), width=2)

    elif motif == "concentric":
        for k in range(1, r.randint(4, 7)):
            d = k * 20
            draw.rectangle([cx - d, cy - d, cx + d, cy + d], outline=(60, 80, 120), width=3)

    else:  # grid
        step = r.randint(20, 35)
        for x in range(0, IMG_SIZE, step):
            draw.line([x, 0, x, IMG_SIZE], fill=(60, 80, 120), width=2)
        for y in range(0, IMG_SIZE, step):
            draw.line([0, y, IMG_SIZE, y], fill=(60, 80, 120), width=2)
        for _ in range(r.randint(3, 6)):
            rx, ry = r.randint(20, IMG_SIZE - 40), r.randint(20, IMG_SIZE - 40)
            rr = r.randint(10, 30)
            draw.ellipse([rx - rr, ry - rr, rx + rr, ry + rr], outline=(120, 40, 80), width=3)

    # textura
    for _ in range(300):
        x, y = r.randint(0, IMG_SIZE - 1), r.randint(0, IMG_SIZE - 1)
        draw.point((x, y), fill=(r.randint(150, 220), r.randint(140, 210), r.randint(160, 230)))
    return img


GENERATORS = {
    "Antropomorfo": make_antropomorfo,
    "Zoomorfo":     make_zoomorfo,
    "Geométrico":   make_geometrico,
}

REFERENCE_NAMES = {
    "Antropomorfo": [
        "Figura antropomorfa frontal", "Figura con tocado ceremonial",
        "Antropomorfo con extremidades extendidas", "Figura humana estilizada",
        "Personaje con adornos", "Antropomorfo danzante",
    ],
    "Zoomorfo": [
        "Figura de serpiente", "Animal cuadrúpedo estilizado",
        "Ave rupestre", "Reptil con cola larga",
        "Mamífero estilizado", "Figura zoomorfa indeterminada",
    ],
    "Geométrico": [
        "Espiral simple", "Círculos concéntricos",
        "Retícula geométrica", "Figura romboidal",
        "Patrón de líneas paralelas", "Motivo en cruz",
    ],
}


def main() -> None:
    records = []
    img_idx = 0

    # Asignar 1–2 taxonomías por sitio al azar y generar N imágenes por combinación
    for site_name, municipality, department in SITES:
        n_tax = random.randint(1, 2)
        site_taxonomies = random.sample(TAXONOMIES, n_tax)
        for taxonomy in site_taxonomies:
            ref_names = REFERENCE_NAMES[taxonomy]
            for k in range(N_PER_SITE_TAX):
                fn = f"syn_{taxonomy[:3].lower()}_{img_idx:04d}.png"
                out_path = OUTPUT_DIR / fn
                img = GENERATORS[taxonomy](img_idx)
                img.save(out_path)
                records.append({
                    "image_path": str(out_path),
                    "site_name": site_name,
                    "municipality": municipality,
                    "taxonomy": taxonomy,
                    "reference_name": ref_names[k % len(ref_names)],
                })
                img_idx += 1
                if img_idx % 20 == 0:
                    print(f"  {img_idx} imágenes generadas...")

    with MANIFEST_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["image_path", "site_name", "municipality", "taxonomy", "reference_name"])
        writer.writeheader()
        writer.writerows(records)

    print(f"\nCorpus generado:")
    print(f"  Imágenes: {img_idx}  →  {OUTPUT_DIR}")
    print(f"  Manifest: {MANIFEST_PATH}")
    print(f"\nSiguiente paso:")
    print(f"  python -m scripts.seed_embeddings --csv {MANIFEST_PATH} --skip-existing")


if __name__ == "__main__":
    main()
