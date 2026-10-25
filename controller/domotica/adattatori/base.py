"""Interfacce che la logica usa. Ogni implementazione è intercambiabile."""
from typing import Protocol

from ..modelli import Campione, LetturaZona, Modalita, StatoPompa


class ErroreAdattatore(Exception):
    """Il dispositivo o il servizio non ha risposto come previsto."""


class FonteSolare(Protocol):
    nome: str

    def leggi(self) -> Campione: ...


class PompaDiCalore(Protocol):
    nome: str

    def stato(self) -> StatoPompa: ...

    def imposta_modalita(self, modalita: Modalita) -> None: ...


class SensoriTemperatura(Protocol):
    """Zone del termostato BTicino via OpenWebNet sul MyHOMEServer1 (adattatore in progettazione).

    Serve alla regia fra termostato e Maxa, che oggi non si parlano.
    """
    nome: str

    def zone(self) -> list[str]: ...

    def leggi(self) -> list[LetturaZona]: ...
