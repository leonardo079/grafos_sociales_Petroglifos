# Frontend — Panel de Pruebas

Interfaz web para ejercer todos los endpoints de la API de Grafos Sociales Rupestres.
HTML/CSS/JS puro, sin build step ni dependencias.

## Requisitos

La API debe estar corriendo (por defecto en `http://localhost:8000`):

```bash
# Desde la raíz del proyecto
uvicorn adapters.inbound.api.main:app --host 0.0.0.0 --port 8000 --reload
```

## Cómo levantar el front

La API ya tiene CORS abierto (`allow_origins=["*"]`), así que sirve el front por HTTP:

```bash
# Desde la carpeta frontend/
python -m http.server 5500
```

Luego abre **http://localhost:5500** en el navegador.

> También puedes abrir `index.html` directamente con doble clic, pero servirlo
> por HTTP evita problemas ocasionales de origen `null`.

Si tu API corre en otro host/puerto, cámbialo en el campo **API base URL**
arriba a la derecha.

## Qué cubre

| Pestaña | Endpoints |
|---|---|
| Sitios | `GET /sites`, `GET /sites?department=&municipality=`, `GET /sites/{id}` |
| Comparar imagen | `POST /compare` |
| Sitios similares | `GET /graph/sites/{id}/similar?top_k=` |
| Análisis del grafo | `GET /graph/pagerank`, `/betweenness`, `/communities`, `/metrics` |
| Visualizaciones | `GET /graph/export` (PyVis), `GET /graph/export/plotly` |
| Grafo JSON | `GET /graph` |
| (barra superior) | `GET /health` |

Las aristas muestran `confidence_level` (low / medium / high) y una marca
`provisional` cuando `is_provisional = true`.
