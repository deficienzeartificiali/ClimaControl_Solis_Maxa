"""Pagina di stato in LAN sulla porta 8080 (stdlib, nessun framework)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def avvia(stato_corrente, storico, pausa, porta: int = 8080) -> ThreadingHTTPServer:
    """stato_corrente() -> dict; pausa(ore: float) mette in pausa l'automazione."""

    class Gestore(BaseHTTPRequestHandler):
        def _json(self, codice: int, dati) -> None:
            corpo = json.dumps(dati, default=str, ensure_ascii=False).encode()
            self.send_response(codice)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(corpo)

        def do_GET(self):
            if self.path in ("/", "/stato"):
                self._json(200, {"stato": stato_corrente(), **storico.riepilogo()})
            else:
                self._json(404, {"errore": "non trovato"})

        def do_POST(self):
            if self.path.startswith("/pausa"):
                ore = float(self.path.partition("ore=")[2] or 4)
                pausa(ore)
                self._json(200, {"pausa_ore": ore})
            else:
                self._json(404, {"errore": "non trovato"})

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", porta), Gestore)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
