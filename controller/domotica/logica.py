"""Macchina a stati Normale/Boost con isteresi e tempi minimi.

La logica è pura: riceve campioni, ora e stato letto della pompa, restituisce una
decisione. Non parla con nessun dispositivo, così si testa con dati finti.

In modalità solo_osserva il chiamante deve passare pompa=None: la pompa reale non
segue le decisioni e sembrerebbe sempre cambiata a mano.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from math import ceil

from .config import Config
from .modelli import Campione, Decisione, Modalita, StatoPompa


@dataclass
class StatoLogica:
    modalita: Modalita = Modalita.NORMALE
    ultimo_cambio: datetime | None = None
    pausa_fino: datetime | None = None
    cambi_per_giorno: dict[date, int] = field(default_factory=dict)


class Logica:
    def __init__(self, cfg: Config, stato: StatoLogica | None = None):
        self.cfg = cfg
        self.r = cfg.boost
        self.stato = stato or StatoLogica()

    # --- API pubblica -------------------------------------------------------

    def decidi(self, campioni: list[Campione], ora: datetime, pompa: StatoPompa | None = None) -> Decisione:
        s = self.stato
        if s.pausa_fino and ora < s.pausa_fino:
            return self._resta(f"pausa manuale fino alle {s.pausa_fino:%H:%M}")

        if pompa and pompa.modalita != s.modalita and self._cambiata_a_mano(pompa):
            s.pausa_fino = ora + timedelta(hours=self.cfg.pausa_manuale_ore)
            s.modalita = pompa.modalita
            return self._resta(f"modalità cambiata dall'app ({pompa.modalita.value}): pausa automatica")

        if self._dati_vecchi(campioni, ora):
            if s.modalita == Modalita.BOOST:
                return self._vai(Modalita.NORMALE, "dati Solis assenti o vecchi")
            return self._resta("dati Solis assenti o vecchi")

        if s.modalita == Modalita.BOOST:
            return self._valuta_uscita(campioni, ora, pompa)
        return self._valuta_ingresso(campioni, ora, pompa)

    def registra_cambio(self, modalita: Modalita, ora: datetime) -> None:
        """Da chiamare quando il comando è stato applicato (o simulato in solo_osserva)."""
        s = self.stato
        s.modalita = modalita
        s.ultimo_cambio = ora
        s.cambi_per_giorno = {ora.date(): s.cambi_per_giorno.get(ora.date(), 0) + 1}

    def surplus_kw(self, c: Campione) -> float:
        carica = c.carica_kw if c.soc >= self.r.soc_min else 0.0
        return c.cessione_kw + carica

    # --- regole -------------------------------------------------------------

    def _valuta_ingresso(self, campioni, ora, pompa) -> Decisione:
        r, s = self.r, self.stato
        if s.cambi_per_giorno.get(ora.date(), 0) >= r.cambi_max_giorno:
            return self._resta("raggiunto il massimo di cambi giornalieri")
        if not (r.fascia_inizio <= ora.time() < r.fascia_fine):
            return self._resta("fuori dalla fascia di boost")
        if s.ultimo_cambio and ora - s.ultimo_cambio < timedelta(minutes=r.pausa_dall_ultimo_cambio_min):
            return self._resta("troppo presto dall'ultimo cambio")
        ultimi = campioni[-r.media_campioni:]
        if len(ultimi) < r.media_campioni:
            return self._resta("campioni insufficienti per la media")
        media = sum(self.surplus_kw(c) for c in ultimi) / len(ultimi)
        soc = campioni[-1].soc
        if self._troppo_caldo(pompa):
            return self._resta("comfort massimo già raggiunto")
        if media >= r.surplus_min_kw and soc >= r.soc_min:
            return self._vai(Modalita.BOOST, f"surplus medio {media:.2f} kW, SOC {soc:.0f}%")
        return self._resta(f"surplus medio {media:.2f} kW, SOC {soc:.0f}%")

    def _valuta_uscita(self, campioni, ora, pompa) -> Decisione:
        r, s = self.r, self.stato
        ultimo = campioni[-1]
        # Uscite di sicurezza: valgono anche prima della durata minima.
        if ultimo.soc < r.soc_sotto:
            return self._vai(Modalita.NORMALE, f"SOC sceso a {ultimo.soc:.0f}%")
        if ora.time() >= r.dopo_le or ora.time() < r.fascia_inizio:
            return self._vai(Modalita.NORMALE, "fine della fascia di boost")
        if self._troppo_caldo(pompa):
            return self._vai(Modalita.NORMALE, "comfort massimo raggiunto")

        if s.ultimo_cambio and ora - s.ultimo_cambio < timedelta(minutes=r.durata_min_min):
            return self._resta("boost in corso, durata minima non raggiunta")
        finestra = campioni[-self._campioni_per(r.per_min):]
        if all(c.prelievo_kw >= r.prelievo_kw for c in finestra):
            return self._vai(Modalita.NORMALE, f"prelievo dalla rete per {r.per_min} min")
        if all(c.scarica_kw >= r.scarica_kw for c in finestra):
            return self._vai(Modalita.NORMALE, f"batteria in scarica per {r.per_min} min")
        return self._resta("boost in corso")

    # --- supporto -----------------------------------------------------------

    def _campioni_per(self, minuti: int) -> int:
        return max(1, ceil(minuti * 60 / self.cfg.intervallo_s))

    def _dati_vecchi(self, campioni, ora) -> bool:
        return not campioni or ora - campioni[-1].ts > timedelta(minutes=self.cfg.dati_vecchi_dopo_min)

    def _troppo_caldo(self, pompa: StatoPompa | None) -> bool:
        limite = self.r.temp_max_c
        return bool(limite is not None and pompa and pompa.temp_ambiente_c is not None
                    and pompa.temp_ambiente_c >= limite)

    def _cambiata_a_mano(self, pompa: StatoPompa) -> bool:
        # Lo stato letto dopo un nostro comando può arrivare in ritardo: 10 minuti di tolleranza.
        ultimo = self.stato.ultimo_cambio
        return ultimo is None or pompa.ts - ultimo > timedelta(minutes=10)

    def _resta(self, motivo: str) -> Decisione:
        return Decisione(self.stato.modalita, motivo, cambia=False)

    def _vai(self, modalita: Modalita, motivo: str) -> Decisione:
        return Decisione(modalita, motivo, cambia=True)
