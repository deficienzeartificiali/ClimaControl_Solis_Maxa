import json
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from domotica import main, web
from domotica.config import carica
from domotica.modelli import Campione, Modalita
from domotica.storico import Storico


class Guasto:
    def leggi(self):
        raise ValueError("risposta SolisCloud illeggibile")


class Pompa:
    def __init__(self):
        self.comandi, self.guasta = [], False

    def imposta_modalita(self, m):
        if self.guasta:
            raise RuntimeError("cloud giù")
        self.comandi.append(m)


class Avvisi:
    def __init__(self):
        self.testi = []

    def invia(self, testo):
        self.testi.append(testo)


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATI", tmp_path)
    monkeypatch.setattr(main, "CONFIG", tmp_path / "config.yaml")
    monkeypatch.setenv("FONTE_SOLARE", "sim")
    monkeypatch.setenv("POMPA", "sim")
    (tmp_path / "config.yaml").write_text("solo_osserva: false\n")
    c = main.Controller(carica(tmp_path / "config.yaml"))
    c.solare, c.pompa, c.avvisi = Guasto(), Pompa(), Avvisi()
    c.logica.registra_cambio(Modalita.BOOST, main.ora() - timedelta(hours=1))
    return c


def test_lettura_solare_guasta_non_ferma_il_ciclo_e_torna_a_normale(controller):
    controller.ciclo()
    assert controller.pompa.comandi == [Modalita.NORMALE]
    assert (main.DATI / "heartbeat").exists()


def test_un_avviso_quando_inizia_e_uno_quando_finisce(controller):
    controller.ciclo()
    controller.ciclo()
    assert len(controller.avvisi.testi) == 1
    adesso = main.ora()
    controller.solare = type("Ok", (), {"leggi": lambda self: Campione(adesso, 3, 1, -1, 0, 95)})()
    controller.ciclo()
    assert controller.avvisi.testi[-1].startswith("Risolto")


def test_comando_fallito_si_riprova_al_ciclo_dopo(controller):
    controller.pompa.guasta = True
    controller.ciclo()
    assert controller.logica.stato.modalita == Modalita.BOOST
    assert "comando" in controller.problemi
    controller.pompa.guasta = False
    controller.ciclo()
    assert controller.pompa.comandi == [Modalita.NORMALE] and "comando" not in controller.problemi


def test_config_non_valida_tiene_quella_vecchia(controller):
    vecchia = controller.cfg
    main.CONFIG.write_text("boost: [\n")
    os.utime(main.CONFIG, (1, 1))
    controller.ciclo()
    assert controller.cfg is vecchia


def test_storico_ordina_offset_misti(tmp_path):
    s = Storico(tmp_path / "s.db")
    roma = timezone(timedelta(hours=1))
    # 12:30 a Roma viene prima delle 11:45 UTC (= 12:45 a Roma): come testo con offset diversi sarebbe al contrario.
    s.salva_campione(Campione(datetime(2026, 11, 3, 11, 45, tzinfo=timezone.utc), 1, 1, 0, 0, 90))
    s.salva_campione(Campione(datetime(2026, 11, 3, 12, 30, tzinfo=roma), 1, 1, 0, 0, 80))
    assert [c.soc for c in s.ultimi_campioni(2)] == [80, 90]


@pytest.fixture
def pagina():
    with socket.socket() as libera:
        libera.bind(("127.0.0.1", 0))
        porta = libera.getsockname()[1]
    pause = []
    server = web.avvia(lambda: {}, type("S", (), {"riepilogo": lambda self: {}})(), pause.append, porta)
    yield f"http://127.0.0.1:{porta}", pause
    server.shutdown()


@pytest.mark.parametrize("query, codice", [("?ore=2", 200), ("", 200), ("?ore=abc", 400), ("?ore=-1", 400)])
def test_pausa_dalla_pagina(pagina, query, codice):
    url, pause = pagina
    try:
        risposta = urllib.request.urlopen(urllib.request.Request(f"{url}/pausa{query}", method="POST"))
        stato = risposta.status
        json.loads(risposta.read())
    except urllib.error.HTTPError as e:
        stato = e.code
    assert stato == codice
    assert len(pause) == (1 if codice == 200 else 0)
