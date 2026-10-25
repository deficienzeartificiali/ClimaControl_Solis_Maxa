"""Prova più adattatori della pompa in ordine; sospende quelli che falliscono di fila."""
import logging
from datetime import datetime, timedelta, timezone

from ..modelli import Modalita, StatoPompa
from .base import ErroreAdattatore, PompaDiCalore

log = logging.getLogger(__name__)


class CatenaPompa:
    nome = "catena"

    def __init__(self, anelli: list[PompaDiCalore], errori_prima_di_sospendere: int = 3,
                 sospensione_min: int = 60, orologio=lambda: datetime.now(timezone.utc)):
        if not anelli:
            raise ValueError("Catena pompa vuota")
        self.anelli = anelli
        self.limite = errori_prima_di_sospendere
        self.sospensione = timedelta(minutes=sospensione_min)
        self.orologio = orologio
        self.errori = {a.nome: 0 for a in anelli}
        self.sospeso_fino: dict[str, datetime] = {}

    def _attivi(self):
        ora = self.orologio()
        return [a for a in self.anelli if self.sospeso_fino.get(a.nome, ora) <= ora]

    def _esegui(self, operazione: str, chiamata):
        motivi = []
        for anello in self._attivi():
            try:
                risultato = chiamata(anello)
                self.errori[anello.nome] = 0
                return risultato
            except ErroreAdattatore as e:
                motivi.append(str(e))
                self.errori[anello.nome] += 1
                log.warning("%s fallito su %s: %s", operazione, anello.nome, e)
                if self.errori[anello.nome] >= self.limite:
                    self.sospeso_fino[anello.nome] = self.orologio() + self.sospensione
                    self.errori[anello.nome] = 0
                    log.error("%s sospeso per %s", anello.nome, self.sospensione)
        raise ErroreAdattatore(f"{operazione}: nessun adattatore disponibile ({'; '.join(motivi) or 'tutti sospesi'})")

    def stato(self) -> StatoPompa:
        return self._esegui("stato", lambda a: a.stato())

    def imposta_modalita(self, modalita: Modalita) -> None:
        self._esegui("imposta_modalita", lambda a: a.imposta_modalita(modalita))
