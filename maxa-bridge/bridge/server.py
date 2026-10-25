"""Ponte HTTP interno verso la web app Maxa Connect, pilotata con Playwright.

Chromium viene avviato solo per la durata di una richiesta e poi chiuso, per stare
nei 2 GB del PC UPS. La sessione (cookie) è salvata nel volume /app/sessione.

API (solo rete interna Docker):
  GET  /stato     -> {"modalita": "normale|boost", "accesa": bool, "temp_ambiente_c": float|null,
                      "temp_esterna_c": float|null}
  POST /modalita  {"modalita": "normale|boost"}

I selettori della web app non sono ancora noti: vanno ricavati aprendo Maxa Connect
dal browser. Finché mancano, le API rispondono 501.
"""
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

URL_WEBAPP = os.environ.get("MAXA_URL", "")  # da compilare quando conosciamo l'indirizzo della web app
SESSIONE = Path("/app/sessione/stato.json")
TIMEOUT_MS = int(os.environ.get("CHROMIUM_TIMEOUT_S", "60")) * 1000
SEGRETI = Path("/run/secrets")

log = logging.getLogger("maxa-bridge")
# Una sola istanza di Chromium alla volta: i comandi sono rari e la RAM è poca.
browser_lock = threading.Lock()


class NonImplementato(Exception):
    pass


def _segreto(nome: str) -> str:
    p = SEGRETI / nome
    return p.read_text().strip() if p.exists() else ""


def _con_pagina(azione):
    from playwright.sync_api import sync_playwright

    if not URL_WEBAPP:
        raise NonImplementato("MAXA_URL non configurato")
    with browser_lock, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--disable-dev-shm-usage"])
        try:
            contesto = browser.new_context(storage_state=str(SESSIONE) if SESSIONE.exists() else None)
            contesto.set_default_timeout(TIMEOUT_MS)
            pagina = contesto.new_page()
            pagina.goto(URL_WEBAPP)
            _login_se_serve(pagina)
            risultato = azione(pagina)
            contesto.storage_state(path=str(SESSIONE))
            return risultato
        finally:
            browser.close()


def _login_se_serve(pagina) -> None:
    # TODO: riconoscere la pagina di login e compilare utente/password
    # con _segreto("maxa_utente") e _segreto("maxa_password").
    raise NonImplementato("login Maxa Connect da implementare")


def leggi_stato(pagina) -> dict:
    # TODO: leggere modalità, accensione e temperature dalla pagina.
    raise NonImplementato("lettura stato da implementare")


def imposta_modalita(pagina, modalita: str) -> None:
    # TODO: boost = setpoint comfort / secondo setpoint; normale = setpoint standard.
    raise NonImplementato("comando modalità da implementare")


class Gestore(BaseHTTPRequestHandler):
    def _json(self, codice: int, dati) -> None:
        corpo = json.dumps(dati, ensure_ascii=False).encode()
        self.send_response(codice)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(corpo)

    def _esegui(self, azione):
        try:
            self._json(200, _con_pagina(azione) or {"ok": True})
        except NonImplementato as e:
            self._json(501, {"errore": str(e)})
        except Exception as e:  # la web app può cambiare in qualsiasi modo
            log.exception("errore web app")
            self._json(502, {"errore": str(e)})

    def do_GET(self):
        if self.path == "/stato":
            self._esegui(leggi_stato)
        elif self.path == "/salute":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"errore": "non trovato"})

    def do_POST(self):
        if self.path != "/modalita":
            return self._json(404, {"errore": "non trovato"})
        lunghezza = int(self.headers.get("Content-Length", 0))
        modalita = json.loads(self.rfile.read(lunghezza) or b"{}").get("modalita")
        if modalita not in ("normale", "boost"):
            return self._json(400, {"errore": "modalita deve essere normale o boost"})
        self._esegui(lambda pagina: imposta_modalita(pagina, modalita))

    def log_message(self, *_):
        pass


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), stream=sys.stdout)
    log.info("maxa-bridge in ascolto su 8090")
    ThreadingHTTPServer(("0.0.0.0", 8090), Gestore).serve_forever()


if __name__ == "__main__":
    main()
