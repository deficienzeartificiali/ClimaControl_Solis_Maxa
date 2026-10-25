import pytest

from domotica.adattatori.base import ErroreAdattatore
from domotica.adattatori.openwebnet import (
    ACK, NACK, Connessione, SensoriOpenWebNet, cifre_in_esadecimale, decodifica_temperatura,
    esadecimale_in_cifre, password_open, risposta_hmac,
)


class GatewayFinto:
    """Socket finto che si comporta come un gateway MyHOME in sessione comandi."""

    def __init__(self, zone: dict[str, str], auth: str = "nessuna", password: str = "12345"):
        self.zone, self.auth, self.password = zone, auth, password
        self.connessioni = 0
        self.__call__(None, None)
        self.connessioni = 0

    def __call__(self, indirizzo, timeout):
        """Ogni nuova connessione riparte dal saluto del gateway."""
        self.uscita, self.stato, self.chiuso = [ACK], "inizio", False
        self.connessioni += 1
        return self

    def sendall(self, dati: bytes):
        msg = dati.decode()
        if self.stato == "inizio":
            assert msg == "*99*0##"
            if self.auth == "nessuna":
                self.uscita.append(ACK)
                self.stato = "pronto"
            elif self.auth == "open":
                self.uscita.append("*#603356072##")
                self.stato = "open"
            else:
                self.uscita.append("*98*2##")
                self.stato = "hmac-ack"
        elif self.stato == "open":
            ok = msg == f"*#{password_open(self.password, '603356072')}##"
            self.uscita.append(ACK if ok else NACK)
            self.stato = "pronto"
        elif self.stato == "hmac-ack":
            assert msg == ACK
            self.ra = "a1" * 32
            self.uscita.append(f"*#{esadecimale_in_cifre(self.ra)}##")
            self.stato = "hmac"
        elif self.stato == "hmac":
            rb_cifre, client = msg[2:-2].split("*")
            atteso, _, conferma = risposta_hmac(self.password, esadecimale_in_cifre(self.ra), "sha256",
                                                cifre_in_esadecimale(rb_cifre))
            self.uscita.append(conferma if msg == atteso else NACK)
            self.stato = "hmac-fine"
        elif self.stato == "hmac-fine":
            assert msg == ACK
            self.stato = "pronto"
        else:
            zona = msg[len("*#4*"):-len("*0##")]
            if zona in self.zone:
                self.uscita += [f"*#4*{zona}*0*{self.zone[zona]}##", ACK]
            else:
                self.uscita.append(NACK)

    def recv(self, _):
        return self.uscita.pop(0).encode() if self.uscita else b""

    def close(self):
        self.chiuso = True


def connessione(gateway, password="12345"):
    return lambda host, porta, pw: Connessione(host, porta, password, apri=gateway)


def test_decodifica_temperatura():
    assert decodifica_temperatura("0215") == 21.5
    assert decodifica_temperatura("1035") == -3.5


def test_codifica_esadecimale_andata_e_ritorno():
    assert esadecimale_in_cifre("a1f") == "100115"
    assert cifre_in_esadecimale("100115") == "a1f"


def test_password_open_vettore_noto():
    assert password_open("12345", "603356072") == "25280520"


@pytest.mark.parametrize("auth", ["nessuna", "open", "hmac"])
def test_autoscoperta_e_lettura_zone(auth):
    gw = GatewayFinto({"1": "0205", "3": "0218"}, auth=auth)
    sensori = SensoriOpenWebNet("192.168.5.10", connetti=connessione(gw), nomi={1: "casa"})
    assert sensori.zone() == ["1", "3"]
    letture = sensori.leggi()
    assert [(z.zona, z.temp_c) for z in letture] == [("casa", 20.5), ("zona 3", 21.8)]
    assert gw.chiuso
    sensori.leggi()
    assert gw.connessioni == 3  # le zone scoperte restano in memoria: niente nuova scansione


def test_password_open_sbagliata():
    gw = GatewayFinto({"1": "0205"}, auth="open")
    with pytest.raises(ErroreAdattatore):
        Connessione("h", 20000, "99999", apri=gw)


def test_password_hmac_sbagliata():
    gw = GatewayFinto({"1": "0205"}, auth="hmac")
    with pytest.raises(ErroreAdattatore):
        Connessione("h", 20000, "99999", apri=gw)


def test_gateway_irraggiungibile():
    def rifiuta(indirizzo, timeout):
        raise ConnectionRefusedError("rifiutata")
    sensori = SensoriOpenWebNet("192.168.5.10", connetti=lambda h, p, pw: Connessione(h, p, pw, apri=rifiuta))
    with pytest.raises(ErroreAdattatore):
        sensori.leggi()
