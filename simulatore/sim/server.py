"""Stub minimo del simulatore: una giornata di sole e una pompa che segue i comandi.

Serve solo a far partire lo stack con il profilo 'sim'. Il simulatore completo
(nuvole, scenari forzati, batteria realistica) è il lavoro del thread dedicato.
"""
import json
import math
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PICCO_PV_KW = 6.0
CARICO_BASE_KW = 0.5
ASSORBIMENTO_BOOST_KW = 2.0

pompa = {"modalita": "normale", "accesa": True, "temp_ambiente_c": 20.5, "temp_esterna_c": 9.0}
casa = {"temp_c": 20.5, "ultimo": None}


def solare(ora: datetime) -> dict:
    h = ora.hour + ora.minute / 60
    pv = max(0.0, PICCO_PV_KW * math.sin(math.pi * (h - 7) / 10)) if 7 <= h <= 17 else 0.0
    carico = CARICO_BASE_KW + (ASSORBIMENTO_BOOST_KW if pompa["modalita"] == "boost" else 0.8)
    soc = 50 + 45 * min(1.0, max(0.0, (h - 8) / 4))
    batteria = min(3.0, pv - carico) if soc < 95 else 0.0
    rete = carico + batteria - pv
    return {"ts": ora.isoformat(), "pv_kw": round(pv, 2), "carico_kw": round(carico, 2),
            "rete_kw": round(rete, 2), "batteria_kw": round(batteria, 2), "soc": round(soc, 1)}


def temperatura_casa(ora: datetime) -> float:
    """La casa si scalda di 0,5 °C/h in boost e si raffredda di 0,2 °C/h in normale (fra 19 e 24 °C)."""
    if casa["ultimo"] is not None:
        ore = (ora - casa["ultimo"]).total_seconds() / 3600
        delta = 0.5 if pompa["modalita"] == "boost" else -0.2
        casa["temp_c"] = min(24.0, max(19.0, casa["temp_c"] + delta * ore))
    casa["ultimo"] = ora
    pompa["temp_ambiente_c"] = round(casa["temp_c"], 1)
    return round(casa["temp_c"], 1)


class Gestore(BaseHTTPRequestHandler):
    def _json(self, dati, codice=200):
        corpo = json.dumps(dati).encode()
        self.send_response(codice)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        ora = datetime.now().astimezone()
        if self.path == "/solare":
            self._json(solare(ora))
        elif self.path == "/temperature":
            self._json([{"ts": ora.isoformat(), "zona": "casa", "temp_c": temperatura_casa(ora)}])
        elif self.path == "/pompa":
            self._json({"ts": ora.isoformat(), **pompa})
        else:
            self._json({"errore": "non trovato"}, 404)

    def do_POST(self):
        if self.path != "/pompa":
            return self._json({"errore": "non trovato"}, 404)
        dati = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        pompa["modalita"] = dati.get("modalita", pompa["modalita"])
        self._json({"ok": True})

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8070), Gestore).serve_forever()
