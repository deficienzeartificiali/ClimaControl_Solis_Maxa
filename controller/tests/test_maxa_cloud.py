import itertools
import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from domotica.adattatori.base import ErroreAdattatore
from domotica.adattatori.maxa_cloud import ClientMqtt, Credenziali, MaxaCloud, chiave_firma, url_mqtt_firmato, valore
from domotica.modelli import Modalita

CRED = Credenziali("AKIDEXAMPLE", "segreto", "token/con+caratteri=", 0, "eu-central-1:id")


def test_chiave_di_firma_vettore_aws():
    # Esempio della documentazione AWS "Deriving the signing key".
    k = chiave_firma("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20120215", "us-east-1", "iam")
    assert k.hex() == "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d"


def test_url_mqtt_firmato():
    url = url_mqtt_firmato(CRED, datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc), host="h.example")
    assert url.startswith("wss://h.example/mqtt?X-Amz-Algorithm=AWS4-HMAC-SHA256&")
    assert "X-Amz-Credential=AKIDEXAMPLE%2F20261025%2Feu-central-1%2Fiotdevicegateway%2Faws4_request" in url
    assert "X-Amz-Date=20261025T120000Z" in url
    # Il token va dopo la firma e codificato.
    assert url.endswith("&X-Amz-Security-Token=token%2Fcon%2Bcaratteri%3D")


@pytest.mark.parametrize("grezzo, atteso", [([21.5], 21.5), (["21.5"], 21.5), ([3276.6], None), (["err"], None),
                                            (["---"], None), ([], None), (None, None), (2, 2.0)])
def test_valori_maxa(grezzo, atteso):
    assert valore(grezzo) == atteso


class SessioneFinta:
    def credenziali(self):
        return CRED


class HttpFinto:
    def __init__(self):
        self.percorsi = []

    def get(self, url, headers, timeout):
        self.percorsi.append(url.split("/prod/v1", 1)[1])
        assert headers["appid"] == "266" and "Authorization" in headers
        dati = {"/get-installation-list": [{"id": "imp-1", "hasAccess": True}],
                "/installation/imp-1/get-details": {"components": [
                    {"componentFn": "connect", "hardwareVersion": "Connect Box"},
                    {"componentFn": "hp-7", "hardwareVersion": "Heat Pump"}]}}[self.percorsi[-1]]
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: dati)


def maxa(parametri, **kw):
    richieste = []

    class ClientFinto:
        def __init__(self, credenziali, installazione, mqtt_v311):
            richieste.append(installazione)

        def richiedi(self, operazioni):
            richieste.append(operazioni)
            return [{"targets": [{"component": "hp-7", "parameters": parametri}]}]

        def chiudi(self):
            pass

    return MaxaCloud(SessioneFinta(), http=HttpFinto(), connetti=ClientFinto, **kw), richieste


def test_stato_trova_impianto_e_pompa():
    m, richieste = maxa({"h200": [2], "h7217": [0], "h428": [8.5], "h401": [35.0], "h8004": ["1.2"]})
    s = m.stato()
    assert (s.modalita, s.accesa, s.temp_esterna_c, s.temp_mandata_c, s.potenza_kw) == (
        Modalita.BOOST, True, 8.5, 35.0, 1.2)
    assert richieste[0] == "imp-1" and richieste[1][0]["targets"][0]["component"] == "hp-7"
    m.stato()
    assert m.http.percorsi == ["/get-installation-list", "/installation/imp-1/get-details"]  # solo la prima volta


def test_economy_e_pompa_spenta():
    s = maxa({"h200": [0], "h7217": [1]})[0].stato()
    assert s.modalita == Modalita.NORMALE and not s.accesa


def test_stato_illeggibile_e_errore():
    with pytest.raises(ErroreAdattatore):
        maxa({"h200": [2], "h7217": ["err"]})[0].stato()


def test_comando_non_ancora_disponibile():
    with pytest.raises(ErroreAdattatore):
        maxa({})[0].imposta_modalita(Modalita.BOOST)


def client_senza_rete(timeout=0.2):
    c = ClientMqtt.__new__(ClientMqtt)
    c.timeout, c.risposte, c.arrivate, c.transazioni = timeout, {}, threading.Condition(), itertools.count(1)
    c.topic_richieste = "imp/cid/installationRequest"
    return c


def test_mqtt_abbina_le_risposte_per_transazione():
    c = client_senza_rete()

    def pubblica(topic, testo):
        tid = json.loads(testo)["transactionId"]
        c._messaggio(None, None, SimpleNamespace(payload=json.dumps({"transactionId": "99"})))
        c._messaggio(None, None, SimpleNamespace(payload=json.dumps({"transactionId": tid, "operations": [1]})))

    c.client = SimpleNamespace(publish=pubblica)
    assert c.richiedi([]) == [1]


def test_mqtt_connect_box_offline():
    c = client_senza_rete()
    c.client = SimpleNamespace(publish=lambda topic, testo: None)
    with pytest.raises(ErroreAdattatore):
        c.richiedi([])
