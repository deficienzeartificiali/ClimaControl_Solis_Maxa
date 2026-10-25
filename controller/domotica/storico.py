"""Storico su SQLite: campioni, decisioni ed eventi."""
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .modelli import Campione, Decisione

SCHEMA = """
CREATE TABLE IF NOT EXISTS campioni (
    ts TEXT PRIMARY KEY, pv_kw REAL, carico_kw REAL, rete_kw REAL, batteria_kw REAL, soc REAL
);
CREATE TABLE IF NOT EXISTS decisioni (
    ts TEXT, modalita TEXT, motivo TEXT, cambia INTEGER, inviato INTEGER
);
CREATE TABLE IF NOT EXISTS eventi (
    ts TEXT, livello TEXT, messaggio TEXT
);
CREATE INDEX IF NOT EXISTS decisioni_ts ON decisioni(ts);
CREATE INDEX IF NOT EXISTS eventi_ts ON eventi(ts);
"""


class Storico:
    def __init__(self, percorso: Path | str):
        self.db = sqlite3.connect(str(percorso), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def salva_campione(self, c: Campione) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO campioni VALUES (?,?,?,?,?,?)",
                            (c.ts.isoformat(), c.pv_kw, c.carico_kw, c.rete_kw, c.batteria_kw, c.soc))

    def salva_decisione(self, ts: datetime, d: Decisione, inviato: bool) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO decisioni VALUES (?,?,?,?,?)",
                            (ts.isoformat(), d.modalita.value, d.motivo, int(d.cambia), int(inviato)))

    def evento(self, ts: datetime, livello: str, messaggio: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO eventi VALUES (?,?,?)", (ts.isoformat(), livello, messaggio))

    def ultimi_campioni(self, n: int) -> list[Campione]:
        with self.lock:
            righe = self.db.execute("SELECT * FROM campioni ORDER BY ts DESC LIMIT ?", (n,)).fetchall()
        return [Campione(ts=datetime.fromisoformat(r["ts"]), pv_kw=r["pv_kw"], carico_kw=r["carico_kw"],
                         rete_kw=r["rete_kw"], batteria_kw=r["batteria_kw"], soc=r["soc"])
                for r in reversed(righe)]

    def riepilogo(self, limite: int = 20) -> dict:
        with self.lock:
            dec = self.db.execute("SELECT * FROM decisioni ORDER BY ts DESC LIMIT ?", (limite,)).fetchall()
            ev = self.db.execute("SELECT * FROM eventi ORDER BY ts DESC LIMIT ?", (limite,)).fetchall()
        return {"decisioni": [dict(r) for r in dec], "eventi": [dict(r) for r in ev]}

    def pulisci(self, ora: datetime, giorni: int) -> None:
        limite = (ora - timedelta(days=giorni)).isoformat()
        with self.lock:
            with self.db:
                for tabella in ("campioni", "decisioni", "eventi"):
                    self.db.execute(f"DELETE FROM {tabella} WHERE ts < ?", (limite,))
            self.db.execute("VACUUM")
