"""Lettura dati dall'API ufficiale SolisCloud.

Firma delle richieste: HMAC-SHA1 su metodo, Content-MD5, Content-Type, Date e percorso,
come da documentazione SolisCloud. I nomi dei campi e i segni di rete e batteria vanno
verificati sulla prima risposta reale: per questo i segni sono configurabili.
"""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from email.utils import format_datetime

import requests

from ..modelli import Campione
from .base import ErroreAdattatore

URL_BASE = "https://www.soliscloud.com:13333"
CONTENT_TYPE = "application/json"


def firma(key_secret: str, corpo: str, data: str, percorso: str) -> tuple[str, str]:
    """Restituisce (Content-MD5, firma base64) per una POST."""
    md5 = base64.b64encode(hashlib.md5(corpo.encode()).digest()).decode()
    testo = "\n".join(["POST", md5, CONTENT_TYPE, data, percorso])
    digest = hmac.new(key_secret.encode(), testo.encode(), hashlib.sha1).digest()
    return md5, base64.b64encode(digest).decode()


def _kw(dati: dict, campo: str) -> float:
    """Converte un valore SolisCloud in kW usando il campo <campo>Str con l'unità."""
    valore = float(dati.get(campo) or 0)
    unita = str(dati.get(f"{campo}Str", "kW")).strip().lower()
    return valore / 1000 if unita == "w" else valore


class SolisCloud:
    nome = "soliscloud"

    def __init__(self, key_id: str, key_secret: str, inverter_sn: str,
                 cessione_positiva: bool = True, carica_positiva: bool = True,
                 url_base: str = URL_BASE, sessione: requests.Session | None = None):
        if not (key_id and key_secret and inverter_sn):
            raise ValueError("SolisCloud: mancano KeyID, KeySecret o numero di serie inverter")
        self.key_id, self.key_secret, self.sn = key_id, key_secret, inverter_sn
        self.cessione_positiva, self.carica_positiva = cessione_positiva, carica_positiva
        self.url_base = url_base
        self.http = sessione or requests.Session()

    def _post(self, percorso: str, corpo: dict) -> dict:
        testo = json.dumps(corpo, separators=(",", ":"))
        data = format_datetime(datetime.now(timezone.utc), usegmt=True)
        md5, sig = firma(self.key_secret, testo, data, percorso)
        intestazioni = {
            "Content-Type": CONTENT_TYPE,
            "Content-MD5": md5,
            "Date": data,
            "Authorization": f"API {self.key_id}:{sig}",
        }
        try:
            risposta = self.http.post(self.url_base + percorso, data=testo, headers=intestazioni, timeout=20)
            risposta.raise_for_status()
            contenuto = risposta.json()
        except (requests.RequestException, ValueError) as e:
            raise ErroreAdattatore(f"SolisCloud {percorso}: {e}") from e
        if not contenuto.get("success", False):
            raise ErroreAdattatore(f"SolisCloud {percorso}: {contenuto.get('code')} {contenuto.get('msg')}")
        return contenuto.get("data") or {}

    def leggi(self) -> Campione:
        d = self._post("/v1/api/inverterDetail", {"sn": self.sn})
        rete = _kw(d, "psum")
        batteria = _kw(d, "batteryPower")
        ts_ms = int(d.get("dataTimestamp") or 0)
        return Campione(
            ts=datetime.fromtimestamp(ts_ms / 1000, timezone.utc) if ts_ms else datetime.now(timezone.utc),
            # pac è l'uscita AC dell'inverter (include la scarica batteria): per il PV serve dcPac.
            pv_kw=_kw(d, "dcPac") if "dcPac" in d else _kw(d, "pac"),
            carico_kw=_kw(d, "familyLoadPower"),
            rete_kw=-rete if self.cessione_positiva else rete,
            batteria_kw=batteria if self.carica_positiva else -batteria,
            soc=float(d.get("batteryCapacitySoc") or 0),
        )
