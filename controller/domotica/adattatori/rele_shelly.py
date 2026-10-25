"""Relè Shelly Gen2 in LAN (API RPC locale) cablato su un ingresso della Maxa.

Cablaggio fail-safe: relè a riposo = Maxa in Normale, relè eccitato = Boost.
"""
from datetime import datetime, timezone

import requests

from ..modelli import Modalita, StatoPompa
from .base import ErroreAdattatore


class ReleShelly:
    nome = "rele"

    def __init__(self, ip: str, canale: int = 0, sessione: requests.Session | None = None):
        if not ip or "DA_COMPILARE" in ip:
            raise ValueError("Relè: IP non configurato")
        self.url = f"http://{ip}/rpc"
        self.canale = canale
        self.http = sessione or requests.Session()

    def _rpc(self, metodo: str, **parametri) -> dict:
        try:
            r = self.http.get(f"{self.url}/{metodo}", params={"id": self.canale, **parametri}, timeout=5)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            raise ErroreAdattatore(f"Shelly {metodo}: {e}") from e

    def stato(self) -> StatoPompa:
        eccitato = bool(self._rpc("Switch.GetStatus").get("output"))
        return StatoPompa(
            ts=datetime.now(timezone.utc),
            modalita=Modalita.BOOST if eccitato else Modalita.NORMALE,
            accesa=True,
        )

    def imposta_modalita(self, modalita: Modalita) -> None:
        on = "true" if modalita == Modalita.BOOST else "false"
        self._rpc("Switch.Set", on=on)
