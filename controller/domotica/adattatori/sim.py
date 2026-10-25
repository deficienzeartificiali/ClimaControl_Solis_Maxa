"""Adattatori verso il container simulatore (profilo Compose 'sim')."""
from datetime import datetime

import requests

from ..modelli import Campione, Modalita, StatoPompa
from .base import ErroreAdattatore


class _ClientSim:
    def __init__(self, url: str, sessione: requests.Session | None = None):
        self.url = url.rstrip("/")
        self.http = sessione or requests.Session()

    def _chiama(self, metodo: str, percorso: str, **kw) -> dict:
        try:
            r = self.http.request(metodo, f"{self.url}{percorso}", timeout=10, **kw)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            raise ErroreAdattatore(f"simulatore {percorso}: {e}") from e


class SolareSim(_ClientSim):
    nome = "sim"

    def leggi(self) -> Campione:
        d = self._chiama("GET", "/solare")
        return Campione(ts=datetime.fromisoformat(d["ts"]), pv_kw=d["pv_kw"], carico_kw=d["carico_kw"],
                        rete_kw=d["rete_kw"], batteria_kw=d["batteria_kw"], soc=d["soc"])


class PompaSim(_ClientSim):
    nome = "sim"

    def stato(self) -> StatoPompa:
        d = self._chiama("GET", "/pompa")
        return StatoPompa(ts=datetime.fromisoformat(d["ts"]), modalita=Modalita(d["modalita"]),
                          accesa=d["accesa"], temp_ambiente_c=d.get("temp_ambiente_c"))

    def imposta_modalita(self, modalita: Modalita) -> None:
        self._chiama("POST", "/pompa", json={"modalita": modalita.value})
