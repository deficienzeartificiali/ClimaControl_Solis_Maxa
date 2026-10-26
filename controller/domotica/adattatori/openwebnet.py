"""Temperature di zona dal gateway BTicino MyHOME (MyHOMEServer1, F454, MH202...) via OpenWebNet.

Sola lettura, sessione comandi su TCP 20000. Supporta gateway senza password,
password OPEN numerica e autenticazione HMAC (SHA-1 o SHA-256). Le zone si scoprono
da sole interrogando gli indirizzi 1-99: OpenWebNet dà i numeri, i nomi delle stanze
si aggiungono in config.yaml.

Da verificare sul gateway reale: tipo di autenticazione e formato degli indirizzi zona.
"""
import hashlib
import logging
import re
import secrets
import socket
from datetime import datetime, timedelta, timezone

from ..modelli import LetturaZona
from .base import ErroreAdattatore

log = logging.getLogger(__name__)

ACK, NACK = "*#*1##", "*#*0##"
SESSIONE_COMANDI = "*99*0##"
RISPOSTA_TEMP = re.compile(r"\*#4\*(\d+)\*0\*(\d{4})##")
HMAC_A, HMAC_B = "736F70653E", "636F70653E"


def decodifica_temperatura(cifre: str) -> float:
    """'0215' -> 21.5; la prima cifra è il segno (0 positivo, 1 negativo)."""
    valore = int(cifre[1:]) / 10
    return -valore if cifre[0] == "1" else valore


def password_open(password: str, nonce: str) -> str:
    """Algoritmo storico OPEN (password numerica) dei gateway BTicino."""
    m = 0xFFFFFFFF
    num1, num2, primo = 0, 0, True
    for c in nonce:
        if c != "0" and primo:
            num2, primo = int(password), False
        if c == "1":
            num1 = ((num2 & 0xFFFFFF80) >> 7) + ((num2 << 25) & m)
        elif c == "2":
            num1 = ((num2 & 0xFFFFFFF0) >> 4) + ((num2 << 28) & m)
        elif c == "3":
            num1 = ((num2 & 0xFFFFFFF8) >> 3) + ((num2 << 29) & m)
        elif c == "4":
            num1 = ((num2 << 1) & m) + (num2 >> 31)
        elif c == "5":
            num1 = ((num2 << 5) & m) + (num2 >> 27)
        elif c == "6":
            num1 = ((num2 << 12) & m) + (num2 >> 20)
        elif c == "7":
            num1 = (num2 & 0x0000FF00) + ((num2 & 0x000000FF) << 24) + ((num2 & 0x00FF0000) >> 16) \
                + ((num2 & 0xFF000000) >> 8)
        elif c == "8":
            num1 = ((num2 & 0x0000FFFF) << 16) + (num2 >> 24) + ((num2 & 0x00FF0000) >> 8)
        elif c == "9":
            num1 = ~num2 & m
        else:
            num1 = num2
        num1 &= m
        num2 = num1
    return str(num1)


def esadecimale_in_cifre(testo: str) -> str:
    """Codifica OpenWebNet: ogni cifra esadecimale diventa due cifre decimali ('a' -> '10')."""
    return "".join(f"{int(c, 16):02d}" for c in testo)


def cifre_in_esadecimale(testo: str) -> str:
    return "".join(f"{int(testo[i:i + 2]):x}" for i in range(0, len(testo), 2))


def risposta_hmac(password: str, ra_cifre: str, algoritmo: str, rb_hex: str | None = None) -> tuple[str, str, str]:
    """Restituisce (messaggio da inviare, rb_hex, conferma attesa dal gateway)."""
    h = (lambda s: hashlib.sha1(s.encode()).hexdigest()) if algoritmo == "sha1" else \
        (lambda s: hashlib.sha256(s.encode()).hexdigest())
    lunghezza = 40 if algoritmo == "sha1" else 64
    ra = cifre_in_esadecimale(ra_cifre)
    rb = rb_hex or h(secrets.token_hex(16))[:lunghezza]
    kab = h(password)
    client = h(ra + rb + HMAC_A + HMAC_B + kab)
    server = h(ra + rb + kab)
    messaggio = f"*#{esadecimale_in_cifre(rb)}*{esadecimale_in_cifre(client)}##"
    return messaggio, rb, f"*#{esadecimale_in_cifre(server)}##"


class Connessione:
    """Una sessione comandi OpenWebNet aperta e autenticata."""

    def __init__(self, host: str, porta: int, password: str, timeout: float = 5.0, apri=socket.create_connection):
        self.sock = apri((host, porta), timeout)
        self.buffer = ""
        try:
            self._attendi(ACK)
            self.invia(SESSIONE_COMANDI)
            self._autentica(password)
        except Exception:
            self.chiudi()
            raise

    def invia(self, messaggio: str) -> None:
        try:
            self.sock.sendall(messaggio.encode())
        except OSError as e:
            raise ErroreAdattatore(f"OpenWebNet: invio fallito ({e})") from e

    def ricevi(self) -> str:
        while "##" not in self.buffer:
            try:
                dati = self.sock.recv(1024)
            except OSError as e:  # comprende il timeout
                raise ErroreAdattatore(f"OpenWebNet: nessuna risposta dal gateway ({e})") from e
            if not dati:
                raise ErroreAdattatore("OpenWebNet: connessione chiusa dal gateway")
            self.buffer += dati.decode(errors="replace")
        messaggio, _, self.buffer = self.buffer.partition("##")
        return messaggio + "##"

    def _attendi(self, atteso: str) -> None:
        ricevuto = self.ricevi()
        if ricevuto != atteso:
            raise ErroreAdattatore(f"OpenWebNet: atteso {atteso}, ricevuto {ricevuto}")

    def _autentica(self, password: str) -> None:
        sfida = self.ricevi()
        if sfida == ACK:
            return
        if sfida in ("*98*1##", "*98*2##"):
            algoritmo = "sha1" if sfida == "*98*1##" else "sha256"
            self.invia(ACK)
            ra = self.ricevi()[2:-2]
            messaggio, _, conferma = risposta_hmac(password, ra, algoritmo)
            self.invia(messaggio)
            if self.ricevi() != conferma:
                raise ErroreAdattatore("OpenWebNet: autenticazione HMAC rifiutata (password errata?)")
            self.invia(ACK)
            return
        if re.fullmatch(r"\*#\d+##", sfida):
            self.invia(f"*#{password_open(password, sfida[2:-2])}##")
            self._attendi(ACK)
            return
        raise ErroreAdattatore(f"OpenWebNet: autenticazione non riconosciuta ({sfida})")

    def richiedi(self, messaggio: str) -> list[str]:
        """Invia una richiesta e raccoglie le risposte fino ad ACK; NACK = lista vuota."""
        self.invia(messaggio)
        risposte = []
        while True:
            r = self.ricevi()
            if r == ACK:
                return risposte
            if r == NACK:
                return []
            risposte.append(r)

    def chiudi(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class SensoriOpenWebNet:
    nome = "openwebnet"

    def __init__(self, host: str, porta: int = 20000, password: str = "", nomi: dict | None = None,
                 riscoperta_ore: float = 24, connetti=Connessione):
        if not host:
            raise ValueError("OpenWebNet: OWN_HOST non configurato")
        self.host, self.porta, self.password = host, porta, password
        self.nomi = {str(k): str(v) for k, v in (nomi or {}).items()}
        self.connetti = connetti
        self.riscoperta = timedelta(hours=riscoperta_ore)
        self._zone: list[str] = []
        self._scoperte_il: datetime | None = None

    def _apri(self) -> Connessione:
        try:
            return self.connetti(self.host, self.porta, self.password)
        except OSError as e:
            raise ErroreAdattatore(f"OpenWebNet {self.host}:{self.porta}: {e}") from e

    def _leggi_zona(self, conn: Connessione, zona: str) -> float | None:
        for r in conn.richiedi(f"*#4*{zona}*0##"):
            m = RISPOSTA_TEMP.fullmatch(r)
            if m and m.group(1) == zona:
                return decodifica_temperatura(m.group(2))
        return None

    def zone(self) -> list[str]:
        ora = datetime.now(timezone.utc)
        if self._zone and self._scoperte_il and ora - self._scoperte_il < self.riscoperta:
            return self._zone
        trovate = []
        conn = self._apri()
        try:
            for z in range(1, 100):
                try:
                    if self._leggi_zona(conn, str(z)) is not None:
                        trovate.append(str(z))
                except ErroreAdattatore as e:
                    # Un gateway che non risponde fermerebbe il ciclo per minuti (99 zone x timeout): si smette.
                    log.warning("scansione zone interrotta alla zona %s: %s", z, e)
                    break
        finally:
            conn.chiudi()
        log.info("zone BTicino trovate: %s", trovate or "nessuna")
        if trovate:  # senza zone si riprova al ciclo successivo invece di aspettare la riscoperta
            self._zone, self._scoperte_il = trovate, ora
        return trovate

    def leggi(self) -> list[LetturaZona]:
        zone = self.zone()
        conn = self._apri()
        ora = datetime.now(timezone.utc)
        try:
            letture = []
            for zona in zone:
                temp = self._leggi_zona(conn, zona)
                if temp is not None:
                    letture.append(LetturaZona(ts=ora, zona=self.nomi.get(zona, f"zona {zona}"), temp_c=temp))
            return letture
        finally:
            conn.chiudi()


class SensoriNessuno:
    """Nessun sensore: la logica usa solo le regole solari."""
    nome = "nessuno"

    def zone(self) -> list[str]:
        return []

    def leggi(self) -> list[LetturaZona]:
        return []
