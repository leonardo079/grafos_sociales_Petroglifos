"""Vocabulario controlado de categorías taxonómicas."""
from enum import StrEnum


class TaxonomyCategory(StrEnum):
    ANTROPOMORFO  = "Antropomorfo"
    ZOOMORFO      = "Zoomorfo"
    GEOMETRICO    = "Geométrico"
    ASTRONOMICO   = "Astronómico"
    FITOMORFO     = "Fitomorfo"
    HIBRIDO       = "Híbrido"
    INDETERMINADO = "Indeterminado"

    @classmethod
    def valid_values(cls) -> list[str]:
        return [c.value for c in cls]

    @classmethod
    def from_str(cls, value: str) -> "TaxonomyCategory":
        for member in cls:
            if member.value.lower() == value.strip().lower():
                return member
        return cls.INDETERMINADO


class ConservationStatus(StrEnum):
    BUENO   = "Bueno"
    REGULAR = "Regular"
    MALO    = "Malo"
    CRITICO = "Crítico"
    PERDIDO = "Perdido"
