import base64
import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path

import pytest

from domotica.adattatori.base import ErroreAdattatore
from domotica.adattatori.catena import CatenaPompa
from domotica.adattatori.soliscloud import SolisCloud, _kw, firma
from domotica.config import carica
from domotica.modelli import Modalita, StatoPompa


def test_firma_soliscloud():
    corpo = '{"sn":"123"}'
    data = "Sun, 25 Oct 2026 16:00:00 GMT"
    md5, sig = firma("segreto", corpo, data, "/v1/api/inverterDetail")
    assert md5 == base64.b64encode(hashlib.md5(corpo.encode()).digest()).decode()
    atteso = hmac.new(b"segreto", f"POST\n{md5}\napplication/json\n{data}\n/v1/api/inverterDetail".encode(),
                      hashlib.sha1).digest()
    assert sig == base64.b64encode(atteso).decode()


def test_unita_in_watt_convertite():
    assert _kw({"pac": 1500, "pacStr": "W"}, "pac") == 1.5
    assert _kw({"pac": 1.5, "pacStr": "kW"}, "pac") == 1.5


class RispostaFinta:
    def __init__(self, dati):
        self.dati = dati

    def raise_for_status(self):
        pass

    def json(self):
        return self.dati


class SessioneFinta:
    def __init__(self, dati):
        self.dati, self.richieste = dati, []

    def post(self, url, data, headers, timeout):
        self.richieste.append((url, headers))
        return RispostaFinta(self.dati)


def test_soliscloud_converte_segni_in_convenzione_interna():
    dati = {"success": True, "code": "0", "data": {
        "pac": 4.2, "pacStr": "kW", "familyLoadPower": 800, "familyLoadPowerStr": "W",
        "psum": 2.1, "psumStr": "kW", "batteryPower": 1.2, "batteryPowerStr": "kW",
        "batteryCapacitySoc": 91, "dataTimestamp": "1793030400000"}}
    sessione = SessioneFinta(dati)
    c = SolisCloud("id", "segreto", "SN1", sessione=sessione).leggi()
    assert (c.pv_kw, c.carico_kw, c.rete_kw, c.batteria_kw, c.soc) == (4.2, 0.8, -2.1, 1.2, 91)
    assert c.ts == datetime.fromtimestamp(1793030400, timezone.utc)
    assert sessione.richieste[0][1]["Authorization"].startswith("API id:")


def test_soliscloud_errore_api():
    sessione = SessioneFinta({"success": False, "code": "Z0001", "msg": "firma"})
    with pytest.raises(ErroreAdattatore):
        SolisCloud("id", "segreto", "SN1", sessione=sessione).leggi()


class PompaFinta:
    def __init__(self, nome, guasta=False):
        self.nome, self.guasta, self.comandi = nome, guasta, []

    def stato(self):
        if self.guasta:
            raise ErroreAdattatore("giù")
        return StatoPompa(ts=datetime.now(timezone.utc), modalita=Modalita.NORMALE, accesa=True)

    def imposta_modalita(self, m):
        if self.guasta:
            raise ErroreAdattatore("giù")
        self.comandi.append(m)


def test_catena_ripiega_sul_secondo_adattatore():
    web, rele = PompaFinta("maxa_web", guasta=True), PompaFinta("rele")
    CatenaPompa([web, rele]).imposta_modalita(Modalita.BOOST)
    assert rele.comandi == [Modalita.BOOST]


def test_catena_sospende_dopo_errori_ripetuti():
    web, rele = PompaFinta("maxa_web", guasta=True), PompaFinta("rele")
    catena = CatenaPompa([web, rele], errori_prima_di_sospendere=3)
    for _ in range(3):
        catena.imposta_modalita(Modalita.BOOST)
    assert "maxa_web" in catena.sospeso_fino
    web.guasta = False
    catena.imposta_modalita(Modalita.NORMALE)
    assert web.comandi == [] and rele.comandi[-1] == Modalita.NORMALE


def test_catena_tutti_guasti_solleva_errore():
    with pytest.raises(ErroreAdattatore):
        CatenaPompa([PompaFinta("a", True), PompaFinta("b", True)]).imposta_modalita(Modalita.BOOST)


def test_config_esempio_si_carica():
    cfg = carica(Path(__file__).resolve().parents[2] / "config" / "config.example.yaml")
    assert cfg.solo_osserva is True
    assert cfg.boost.surplus_min_kw == 1.5
    assert cfg.boost.fascia_inizio.hour == 9
