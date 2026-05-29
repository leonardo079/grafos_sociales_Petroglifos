"""Entidades de dominio: sitio rupestre y geolocalización."""
from __future__ import annotations
from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass
class GeoLocation:
    latitude: float
    longitude: float
    altitude_m: float | None = None
    accuracy_m: float | None = None


@dataclass
class RupestranSite:
    """Nodo del grafo social: sitio arqueológico rupestre."""
    id: UUID = field(default_factory=uuid4)
    name: str = ""
    municipality: str = ""
    department: str = ""
    location: GeoLocation | None = None
    conservation_status: str = ""
    petroglyph_count: int = 0
    dominant_taxonomy: str = ""
    similar_sites: list[dict] = field(default_factory=list)
