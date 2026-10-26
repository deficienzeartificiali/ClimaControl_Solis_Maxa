"""Tipi di dato condivisi fra logica, adattatori e storico.

Convenzioni di segno interne (gli adattatori convertono da quelle dei fornitori):
- rete_kw: positivo = prelievo dalla rete, negativo = cessione alla rete
- batteria_kw: positivo = in carica, negativo = in scarica
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Modalita(str, Enum):
    NORMALE = "normale"
    BOOST = "boost"


@dataclass(frozen=True)
class Campione:
    ts: datetime
    pv_kw: float
    carico_kw: float
    rete_kw: float
    batteria_kw: float
    soc: float

    @property
    def cessione_kw(self) -> float:
        return max(0.0, -self.rete_kw)

    @property
    def prelievo_kw(self) -> float:
        return max(0.0, self.rete_kw)

    @property
    def carica_kw(self) -> float:
        return max(0.0, self.batteria_kw)

    @property
    def scarica_kw(self) -> float:
        return max(0.0, -self.batteria_kw)

    @property
    def deficit_kw(self) -> float:
        """Quanto la casa sta prendendo da rete e batteria insieme: in boost deve restare vicino a zero."""
        return self.prelievo_kw + self.scarica_kw


@dataclass(frozen=True)
class StatoPompa:
    ts: datetime
    modalita: Modalita
    accesa: bool
    temp_ambiente_c: float | None = None
    temp_mandata_c: float | None = None
    temp_esterna_c: float | None = None
    potenza_kw: float | None = None


@dataclass(frozen=True)
class Decisione:
    modalita: Modalita
    motivo: str
    cambia: bool


@dataclass(frozen=True)
class LetturaZona:
    """Temperatura di una zona dal termostato BTicino MyHOME (o dal simulatore)."""
    ts: datetime
    zona: str
    temp_c: float
