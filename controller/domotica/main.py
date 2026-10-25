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
from .adattatori import ErroreAdattatore, crea_fonte_solare, crea_pompa
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
        self.logica = Logica(cfg)
        self.avvisi = Avvisi(cfg.avvisi.get("tipo", "nessuno"), cfg.avvisi.get("canale", ""),
                             configurazione.segreto("avvisi_token"))
        self.ultimo_stato_pompa = None
        self.config_mtime = CONFIG.stat().st_mtime if CONFIG.exists() else 0

    def ricarica_config_se_cambiata(self) -> None:
        if CONFIG.exists() and CONFIG.stat().st_mtime != self.config_mtime:
            self.config_mtime = CONFIG.stat().st_mtime
            self.cfg = configurazione.carica(CONFIG)
            self.logica = Logica(self.cfg, self.logica.stato)
            log.info("config.yaml ricaricato")

    def ciclo(self) -> None:
        adesso = ora()
        self.ricarica_config_se_cambiata()
        try:
            self.storico.salva_campione(self.solare.leggi())
        except ErroreAdattatore as e:
            log.warning("lettura solare fallita: %s", e)
            self.storico.evento(adesso, "warning", str(e))

        campioni = self.storico.ultimi_campioni(12)
        pompa = None if self.cfg.solo_osserva else self.ultimo_stato_pompa
        decisione = self.logica.decidi(campioni, adesso, pompa)
        inviato = False
        if decisione.cambia:
            if self.cfg.solo_osserva:
                self.logica.registra_cambio(decisione.modalita, adesso)
            else:
                try:
                    self.pompa.imposta_modalita(decisione.modalita)
                    self.logica.registra_cambio(decisione.modalita, adesso)
                    inviato = True
                except ErroreAdattatore as e:
                    log.error("comando pompa fallito: %s", e)
                    self.storico.evento(adesso, "error", str(e))
                    self.avvisi.invia(f"Comando Maxa non riuscito: {e}")
        self.storico.salva_decisione(adesso, decisione, inviato)
        log.info("decisione %s (%s) cambia=%s inviato=%s", decisione.modalita.value, decisione.motivo,
                 decisione.cambia, inviato)

    def leggi_stato_pompa(self) -> None:
        try:
            self.ultimo_stato_pompa = self.pompa.stato()
        except ErroreAdattatore as e:
            log.warning("stato pompa non letto: %s", e)

    def pausa(self, ore: float) -> None:
        self.logica.stato.pausa_fino = ora() + timedelta(hours=ore)
        if self.logica.stato.modalita == Modalita.BOOST and not self.cfg.solo_osserva:
            try:
                self.pompa.imposta_modalita(Modalita.NORMALE)
                self.logica.registra_cambio(Modalita.NORMALE, ora())
            except ErroreAdattatore as e:
                log.error("ritorno a Normale fallito: %s", e)

    def stato_corrente(self) -> dict:
        s = self.logica.stato
        return {"modalita": s.modalita.value, "pausa_fino": s.pausa_fino, "ultimo_cambio": s.ultimo_cambio,
                "solo_osserva": self.cfg.solo_osserva, "pompa": asdict(self.ultimo_stato_pompa) if self.ultimo_stato_pompa else None}

    def avvio_sicuro(self) -> None:
        """Allo start legge lo stato reale prima di decidere: non presume nulla."""
        self.leggi_stato_pompa()
        if self.ultimo_stato_pompa:
            self.logica.stato.modalita = self.ultimo_stato_pompa.modalita


def heartbeat() -> None:
    (DATI / "heartbeat").touch()


def main() -> None:
    gestore = logging.StreamHandler(sys.stdout)
    gestore.setFormatter(FormatoJson())
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), handlers=[gestore])
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    DATI.mkdir(parents=True, exist_ok=True)
    cfg = configurazione.carica(CONFIG)
    c = Controller(cfg)
    if not cfg.solo_osserva:
        c.avvio_sicuro()
    web.avvia(c.stato_corrente, c.storico, c.pausa)
    heartbeat()

    sched = BlockingScheduler(job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 60})
    sched.add_job(c.ciclo, "interval", seconds=cfg.intervallo_s, next_run_time=ora() + timedelta(seconds=5))
    sched.add_job(c.leggi_stato_pompa, "interval", minutes=cfg.maxa.get("lettura_stato_min", 15))
    sched.add_job(heartbeat, "interval", minutes=1)
    sched.add_job(lambda: c.storico.pulisci(ora(), cfg.storico.get("conserva_giorni", 400)), "cron", hour=3)
    log.info("controller avviato (solo_osserva=%s)", cfg.solo_osserva)
    sched.start()


if __name__ == "__main__":
    main()
