"""Comando della Maxa tramite il servizio maxa-bridge (Playwright sulla web app)."""
from datetime import datetime, timezone

import requests

from ..modelli import Modalita, StatoPompa
from .base import ErroreAdattatore


class MaxaWeb:
    nome = "maxa_web"

    def __init__(self, url_bridge: str, sessione: requests.Session | None = None):
        self.url = url_bridge.rstrip("/")
        self.http = sessione or requests.Session()

    def stato(self) -> StatoPompa:
        try:
            r = self.http.get(f"{self.url}/stato", timeout=90)
            r.raise_for_status()
            d = r.json()
        except (requests.RequestException, ValueError) as e:
            raise ErroreAdattatore(f"maxa-bridge /stato: {e}") from e
        return StatoPompa(
            ts=datetime.now(timezone.utc),
            modalita=Modalita(d["modalita"]),
            accesa=bool(d.get("accesa", True)),
            temp_ambiente_c=d.get("temp_ambiente_c"),
            temp_mandata_c=d.get("temp_mandata_c"),
            temp_esterna_c=d.get("temp_esterna_c"),
        )

    def imposta_modalita(self, modalita: Modalita) -> None:
        try:
            r = self.http.post(f"{self.url}/modalita", json={"modalita": modalita.value}, timeout=90)
            r.raise_for_status()
        except requests.RequestException as e:
            raise ErroreAdattatore(f"maxa-bridge /modalita: {e}") from e
