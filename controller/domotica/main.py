"""Punto di ingresso del controller: collega adattatori, logica, storico e scheduler."""
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from . import config as configurazione
from . import web
from .adattatori import crea_fonte_solare, crea_pompa, crea_sensori
from .avvisi import Avvisi
from .logica import Logica
from .modelli import Modalita
from .storico import Storico

DATI = Path(os.environ.get("CARTELLA_DATI", "/app/dati"))
CONFIG = Path(os.environ.get("FILE_CONFIG", "/app/config/config.yaml"))


class FormatoJson(logging.Formatter):
    def format(self, record):
        return json.dumps({"ts": self.formatTime(record), "servizio": "controller", "livello": record.levelname,
                           "modulo": record.name, "messaggio": record.getMessage()}, ensure_ascii=False)


log = logging.getLogger("controller")


def ora() -> datetime:
    return datetime.now().astimezone()


class Controller:
    def __init__(self, cfg: configurazione.Config):
        self.cfg = cfg
        self.storico = Storico(DATI / "storico.db")
        self.solare = crea_fonte_solare(cfg)
        self.pompa = crea_pompa(cfg)
        self.sensori = crea_sensori(cfg)
        self.ultime_temperature = []
        self.logica = Logica(cfg)
        self.avvisi = Avvisi(cfg.avvisi.get("tipo", "nessuno"), cfg.avvisi.get("canale", ""),
                             configurazione.segreto("avvisi_token"))
        self.ultimo_stato_pompa = None
        self.temp_casa = None
        self.problemi: set[str] = set()
        self.config_mtime = CONFIG.stat().st_mtime if CONFIG.exists() else 0

    def ricarica_config_se_cambiata(self) -> None:
        if CONFIG.exists() and CONFIG.stat().st_mtime != self.config_mtime:
            self.config_mtime = CONFIG.stat().st_mtime
            try:
                self.cfg = configurazione.carica(CONFIG)
            except Exception as e:
                log.error("config.yaml non valido, resta in uso quello precedente: %s", e)
                return
            self.logica = Logica(self.cfg, self.logica.stato)
            log.info("config.yaml ricaricato (adattatori e intervalli cambiano solo al riavvio)")

    def _prova(self, cosa: str, funzione, adesso: datetime):
        """Una lettura o un comando che fallisce, per qualsiasi motivo, non deve fermare il ciclo."""
        try:
            return funzione()
        except Exception as e:
            log.warning("%s fallito: %s", cosa, e)
            self.storico.evento(adesso, "warning", f"{cosa}: {e}")
            return None

    def _segnala(self, chiave: str, attivo: bool, testo: str) -> None:
        """Un avviso quando un problema inizia e uno quando finisce, non uno a ogni ciclo."""
        if attivo and chiave not in self.problemi:
            self.problemi.add(chiave)
            self.avvisi.invia(testo)
        elif not attivo and chiave in self.problemi:
            self.problemi.discard(chiave)
            self.avvisi.invia(f"Risolto: {testo}")

    def leggi_temperature(self, adesso: datetime) -> float | None:
        """Temperatura di casa: media delle zone lette di recente, None se vecchia o assente."""
        letture = self._prova("lettura temperature", self.sensori.leggi, adesso)
        if letture:
            self.ultime_temperature = letture
            self.storico.salva_temperature(letture)
        limite = timedelta(minutes=self.cfg.sensori.get("dati_vecchi_dopo_min", 15))
        recenti = [z.temp_c for z in self.ultime_temperature if adesso - z.ts <= limite]
        return round(sum(recenti) / len(recenti), 2) if recenti else None

    def ciclo(self) -> None:
        adesso = ora()
        self.ricarica_config_se_cambiata()
        campione = self._prova("lettura solare", self.solare.leggi, adesso)
        if campione:
            self.storico.salva_campione(campione)

        campioni = self.storico.ultimi_campioni(12)
        vecchi = not campioni or adesso - campioni[-1].ts > timedelta(minutes=self.cfg.dati_vecchi_dopo_min)
        self._segnala("solis", vecchi, "dati del fotovoltaico assenti: la Maxa resta in Normale")
        pompa = None if self.cfg.solo_osserva else self.ultimo_stato_pompa
        self.temp_casa = self.leggi_temperature(adesso)
        decisione = self.logica.decidi(campioni, adesso, pompa, self.temp_casa)
        inviato = False
        if decisione.cambia:
            if self.cfg.solo_osserva:
                self.logica.registra_cambio(decisione.modalita, adesso)
            else:
                # Se il comando fallisce lo stato logico non cambia e il ciclo successivo riprova.
                try:
                    self.pompa.imposta_modalita(decisione.modalita)
                    self.logica.registra_cambio(decisione.modalita, adesso)
                    inviato = True
                except Exception as e:
                    log.error("comando pompa fallito: %s", e)
                    self.storico.evento(adesso, "error", f"comando pompa: {e}")
                self._segnala("comando", not inviato, "la Maxa non riceve i comandi del controller")
        self.storico.salva_decisione(adesso, decisione, inviato)
        log.info("decisione %s (%s) cambia=%s inviato=%s", decisione.modalita.value, decisione.motivo,
                 decisione.cambia, inviato)
        heartbeat()

    def leggi_stato_pompa(self) -> None:
        stato = self._prova("lettura stato pompa", self.pompa.stato, ora())
        if stato:
            self.ultimo_stato_pompa = stato
            self.storico.salva_pompa(stato)

    def pausa(self, ore: float) -> None:
        self.logica.stato.pausa_fino = ora() + timedelta(hours=ore)
        if self.logica.stato.modalita == Modalita.BOOST and not self.cfg.solo_osserva:
            try:
                self.pompa.imposta_modalita(Modalita.NORMALE)
                self.logica.registra_cambio(Modalita.NORMALE, ora())
            except Exception as e:
                log.error("ritorno a Normale fallito: %s", e)

    def stato_corrente(self) -> dict:
        s = self.logica.stato
        pompa = self.ultimo_stato_pompa
        return {"modalita": s.modalita.value, "pausa_fino": s.pausa_fino, "ultimo_cambio": s.ultimo_cambio,
                "solo_osserva": self.cfg.solo_osserva, "problemi": sorted(self.problemi),
                "temp_casa_c": self.temp_casa, "zone": [asdict(z) for z in self.ultime_temperature],
                "pompa": asdict(pompa) if pompa else None}


def heartbeat() -> None:
    """Toccato a fine ciclo: il healthcheck di Docker vede se le decisioni girano davvero."""
    (DATI / "heartbeat").touch()


def main() -> None:
    gestore = logging.StreamHandler(sys.stdout)
    gestore.setFormatter(FormatoJson())
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), handlers=[gestore])
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    DATI.mkdir(parents=True, exist_ok=True)
    cfg = configurazione.carica(CONFIG)
    c = Controller(cfg)
    c.leggi_stato_pompa()
    if not cfg.solo_osserva and c.ultimo_stato_pompa:
        # Si riparte dallo stato reale della pompa, non da un'ipotesi.
        c.logica.stato.modalita = c.ultimo_stato_pompa.modalita
    web.avvia(c.stato_corrente, c.storico, c.pausa)
    heartbeat()

    sched = BlockingScheduler(job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 60})
    sched.add_job(c.ciclo, "interval", seconds=cfg.intervallo_s, next_run_time=ora() + timedelta(seconds=5))
    sched.add_job(c.leggi_stato_pompa, "interval", minutes=cfg.maxa.get("lettura_stato_min", 15))
    sched.add_job(lambda: c.storico.pulisci(ora(), cfg.storico.get("conserva_giorni", 400)), "cron", hour=3)
    log.info("controller avviato (solo_osserva=%s)", cfg.solo_osserva)
    sched.start()


if __name__ == "__main__":
    main()
