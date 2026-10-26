"""Maxa Connect via cloud, senza browser: le stesse chiamate che fa la web app.

La web app https://start.maxaconnect.com usa servizi AWS in eu-central-1:
- login Cognito (USER_SRP_AUTH) con email e password dell'account Maxa Connect
- credenziali temporanee da un Cognito Identity Pool
- API REST su API Gateway, firmate SigV4
- lettura e scrittura dei registri della pompa via AWS IoT MQTT su WebSocket

Registri (profilo "Heat Pump/Connect"): h200 stagione, h7217 Comfort/Economy attuale,
h7202 Comfort/Economy da impostare, h428 temperatura esterna, h401/h400 acqua in uscita/ingresso,
h242 temperatura impostata, h8004 potenza elettrica assorbita.
Dettagli in /mnt/project-files/architettura/maxa-connect-api.md del progetto.

Da verificare sull'impianto reale: identificativo del componente pompa, scala dei valori
e operazione di scrittura (finché non la conosciamo questo adattatore legge soltanto).
"""
import hashlib
import hmac
import itertools
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from ..modelli import Modalita, StatoPompa
from .base import ErroreAdattatore

log = logging.getLogger(__name__)

REGIONE = "eu-central-1"
USER_POOL = "eu-central-1_que8JCEVH"
CLIENT_ID = "1hu5rj9a75f6ria3ng03gocofo"
IDENTITY_POOL = "eu-central-1:d1f8d4ae-7401-4d69-b603-1e992f635253"
URL_API = "https://6tx17a8ijh.execute-api.eu-central-1.amazonaws.com/prod/v1"
HOST_IOT = "a24t7r3f2r1nrr-ats.iot.eu-central-1.amazonaws.com"
APP_ID = "266"

REGISTRI_STATO = ["h200", "h7217", "h428", "h401", "h400", "h242", "h8004"]
NON_VALIDI = {3276.6, 3276.7}  # "---" e "errore" del profilo


@dataclass
class Credenziali:
    access_key: str
    secret_key: str
    token: str
    scadenza: float
    identity_id: str


def _sha256(testo: str) -> str:
    return hashlib.sha256(testo.encode()).hexdigest()


def _hmac(chiave: bytes, testo: str) -> bytes:
    return hmac.new(chiave, testo.encode(), hashlib.sha256).digest()


def chiave_firma(secret_key: str, giorno: str, regione: str, servizio: str) -> bytes:
    chiave = _hmac(("AWS4" + secret_key).encode(), giorno)
    for parte in (regione, servizio, "aws4_request"):
        chiave = _hmac(chiave, parte)
    return chiave


def url_mqtt_firmato(c: Credenziali, ora: datetime, host: str = HOST_IOT, regione: str = REGIONE) -> str:
    """URL WebSocket presignato SigV4 per AWS IoT; il token di sessione va aggiunto dopo la firma."""
    data_ora = ora.strftime("%Y%m%dT%H%M%SZ")
    giorno = ora.strftime("%Y%m%d")
    ambito = f"{giorno}/{regione}/iotdevicegateway/aws4_request"
    query = "&".join([
        "X-Amz-Algorithm=AWS4-HMAC-SHA256",
        f"X-Amz-Credential={quote(c.access_key + '/' + ambito, safe='')}",
        f"X-Amz-Date={data_ora}",
        "X-Amz-SignedHeaders=host",
    ])
    canonica = "\n".join(["GET", "/mqtt", query, f"host:{host}", "", "host", _sha256("")])
    da_firmare = "\n".join(["AWS4-HMAC-SHA256", data_ora, ambito, _sha256(canonica)])
    firma = hmac.new(chiave_firma(c.secret_key, giorno, regione, "iotdevicegateway"), da_firmare.encode(), hashlib.sha256).hexdigest()
    return f"wss://{host}/mqtt?{query}&X-Amz-Signature={firma}&X-Amz-Security-Token={quote(c.token, safe='')}"


class SessioneAws:
    """Login Cognito e credenziali temporanee, rinnovate da sole prima della scadenza."""

    def __init__(self, utente: str, password: str, idp=None, identity=None, orologio=time.time):
        if not (utente and password):
            raise ValueError("Maxa Connect: mancano utente o password")
        import boto3
        from botocore.config import Config as BotoConfig
        cfg = BotoConfig(region_name=REGIONE, retries={"max_attempts": 2})
        self.utente, self.password = utente, password
        self.idp = idp or boto3.client("cognito-idp", config=cfg)
        self.identity = identity or boto3.client("cognito-identity", config=cfg)
        self.orologio = orologio
        self.id_token: str | None = None
        self.refresh_token: str | None = None
        self.token_scade = 0.0
        self.identity_id: str | None = None
        self._credenziali: Credenziali | None = None
        self.lock = threading.Lock()

    def _login(self) -> None:
        from pycognito.aws_srp import AWSSRP
        srp = AWSSRP(username=self.utente, password=self.password, pool_id=USER_POOL,
                     client_id=CLIENT_ID, client=self.idp)
        self._salva_token(srp.authenticate_user(client_metadata={"appId": APP_ID})["AuthenticationResult"])
        log.info("login Maxa Connect riuscito")

    def _rinnova(self) -> None:
        risposta = self.idp.initiate_auth(AuthFlow="REFRESH_TOKEN_AUTH", ClientId=CLIENT_ID,
                                          AuthParameters={"REFRESH_TOKEN": self.refresh_token})
        self._salva_token(risposta["AuthenticationResult"])

    def _salva_token(self, risultato: dict) -> None:
        self.id_token = risultato["IdToken"]
        self.refresh_token = risultato.get("RefreshToken", self.refresh_token)
        self.token_scade = self.orologio() + int(risultato.get("ExpiresIn", 3600))

    def _token_valido(self) -> str:
        if self.id_token and self.orologio() < self.token_scade - 300:
            return self.id_token
        try:
            if self.refresh_token:
                self._rinnova()
            else:
                self._login()
        except Exception as e:  # refresh scaduto o revocato: si rifà il login completo
            log.info("rinnovo token fallito (%s), nuovo login", e)
            self.refresh_token = None
            self._login()
        return self.id_token

    def credenziali(self) -> Credenziali:
        with self.lock:
            if self._credenziali and self.orologio() < self._credenziali.scadenza - 300:
                return self._credenziali
            try:
                token = self._token_valido()
                logins = {f"cognito-idp.{REGIONE}.amazonaws.com/{USER_POOL}": token}
                if not self.identity_id:
                    self.identity_id = self.identity.get_id(IdentityPoolId=IDENTITY_POOL, Logins=logins)["IdentityId"]
                c = self.identity.get_credentials_for_identity(IdentityId=self.identity_id, Logins=logins)["Credentials"]
            except Exception as e:
                raise ErroreAdattatore(f"Maxa Connect login: {e}") from e
            scadenza = c["Expiration"]
            scadenza = scadenza.timestamp() if isinstance(scadenza, datetime) else float(scadenza)
            self._credenziali = Credenziali(c["AccessKeyId"], c["SecretKey"], c["SessionToken"], scadenza,
                                            self.identity_id)
            return self._credenziali


class ClientMqtt:
    """Una connessione MQTT breve: si apre, si fanno le richieste, si chiude."""

    def __init__(self, credenziali: Credenziali, installazione: str, timeout: float = 20, mqtt_v311: bool = False):
        import paho.mqtt.client as mqtt
        self.timeout = timeout
        self.client_id = f"{credenziali.identity_id}-{int(time.time() * 1000)}"
        base = f"{installazione}/{self.client_id}"
        self.topic_richieste, self.topic_risposte = f"{base}/installationRequest", f"{base}/installationResponse"
        self.risposte: dict[str, dict] = {}
        self.arrivate = threading.Condition()
        self.transazioni = itertools.count(1)
        url = url_mqtt_firmato(credenziali, datetime.now(timezone.utc))
        # La web app usa MQTT 3.1; se AWS IoT rifiuta la connessione si prova maxa.mqtt_v311: true.
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id, transport="websockets",
                                  protocol=mqtt.MQTTv311 if mqtt_v311 else mqtt.MQTTv31)
        self.client.ws_set_options(path=url.split(HOST_IOT, 1)[1], headers={"Origin": "https://start.maxaconnect.com"})
        self.client.tls_set()
        self.client.on_message = self._messaggio
        pronto, self.rifiuto = threading.Event(), None

        def connesso(client, _userdata, _flags, codice, _proprieta=None):
            if codice.is_failure:
                self.rifiuto = str(codice)
                pronto.set()
            else:
                client.subscribe(self.topic_risposte, qos=0)

        self.client.on_connect = connesso
        self.client.on_subscribe = lambda *a, **k: pronto.set()
        try:
            self.client.connect(HOST_IOT, 443, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            self.chiudi()
            raise ErroreAdattatore(f"Maxa Connect MQTT: {e}") from e
        if not pronto.wait(timeout) or self.rifiuto:
            self.chiudi()
            raise ErroreAdattatore(f"Maxa Connect MQTT: connessione non riuscita ({self.rifiuto or 'timeout'})")

    def _messaggio(self, _client, _userdata, msg) -> None:
        try:
            dati = json.loads(msg.payload)
        except ValueError:
            return
        with self.arrivate:
            self.risposte[str(dati.get("transactionId"))] = dati
            self.arrivate.notify_all()

    def richiedi(self, operazioni: list[dict]) -> list[dict]:
        tid = str(next(self.transazioni))
        self.client.publish(self.topic_richieste, json.dumps({"transactionId": tid, "operations": operazioni}))
        with self.arrivate:
            if not self.arrivate.wait_for(lambda: tid in self.risposte, self.timeout):
                raise ErroreAdattatore("Maxa Connect: nessuna risposta dalla Connect Box (offline?)")
            return self.risposte.pop(tid).get("operations", [])

    def chiudi(self) -> None:
        try:
            self.client.disconnect()
            self.client.loop_stop()
        except Exception:
            pass


def valore(grezzo) -> float | None:
    """I valori arrivano come lista [valore, ...], anche come testo; "---", "err" e simili diventano None."""
    v = grezzo[0] if isinstance(grezzo, list) and grezzo else grezzo
    try:
        numero = float(v)
    except (TypeError, ValueError):
        return None
    return None if numero in NON_VALIDI else numero


class MaxaCloud:
    nome = "maxa_cloud"

    def __init__(self, sessione: SessioneAws, installazione: str = "", componente: str = "",
                 comfort_e_boost: bool = True, mqtt_v311: bool = False,
                 http: requests.Session | None = None, connetti=ClientMqtt):
        self.sessione = sessione
        self.installazione, self.componente = installazione, componente
        self.comfort_e_boost = comfort_e_boost
        self.http = http or requests.Session()
        self.connetti = connetti
        self.mqtt_v311 = mqtt_v311

    # --- REST ---------------------------------------------------------------

    def _get(self, percorso: str):
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials
        c = self.sessione.credenziali()
        richiesta = AWSRequest(method="GET", url=f"{URL_API}{percorso}", headers={"appid": APP_ID})
        SigV4Auth(Credentials(c.access_key, c.secret_key, c.token), "execute-api", REGIONE).add_auth(richiesta)
        try:
            r = self.http.get(richiesta.url, headers=dict(richiesta.headers), timeout=20)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            raise ErroreAdattatore(f"Maxa Connect {percorso}: {e}") from e

    def _trova_impianto(self) -> None:
        if not self.installazione:
            impianti = [i for i in self._get("/get-installation-list") if i.get("hasAccess")]
            if not impianti:
                raise ErroreAdattatore("Maxa Connect: nessun impianto sull'account")
            self.installazione = impianti[0]["id"]
            log.info("impianto Maxa trovato: %s", self.installazione)
        if not self.componente:
            dettagli = self._get(f"/installation/{self.installazione}/get-details")
            pompe = [c for c in dettagli.get("components", []) if c.get("hardwareVersion") == "Heat Pump"]
            if not pompe:
                raise ErroreAdattatore("Maxa Connect: nessuna pompa di calore tra i componenti")
            self.componente = pompe[0]["componentFn"]
            log.info("componente pompa Maxa: %s", self.componente)

    # --- MQTT ---------------------------------------------------------------

    def _richiedi(self, operazioni: list[dict]) -> list[dict]:
        client = self.connetti(self.sessione.credenziali(), self.installazione, mqtt_v311=self.mqtt_v311)
        try:
            return client.richiedi(operazioni)
        finally:
            client.chiudi()

    def leggi_registri(self, registri: list[str]) -> dict:
        self._trova_impianto()  # prima di comporre la richiesta, che contiene il componente
        operazioni = self._richiedi([{"name": "GET_VALUES",
                                      "targets": [{"component": self.componente, "parameters": registri}]}])
        for op in operazioni:
            for target in op.get("targets", []):
                if target.get("component") == self.componente:
                    log.debug("valori Maxa grezzi: %s", target.get("parameters"))
                    return target.get("parameters", {})
        raise ErroreAdattatore(f"Maxa Connect: risposta senza valori ({operazioni})")

    def stato(self) -> StatoPompa:
        v = {k: valore(x) for k, x in self.leggi_registri(REGISTRI_STATO).items()}
        economy, stagione = v.get("h7217"), v.get("h200")
        if economy is None:
            raise ErroreAdattatore("Maxa Connect: stato Comfort/Economy non leggibile")
        comfort = (int(economy) & 1) == 0
        boost = comfort if self.comfort_e_boost else not comfort
        return StatoPompa(
            ts=datetime.now(timezone.utc),
            modalita=Modalita.BOOST if boost else Modalita.NORMALE,
            accesa=stagione is not None and int(stagione) != 0,  # 0 = OFF
            temp_mandata_c=v.get("h401"),
            temp_esterna_c=v.get("h428"),
            potenza_kw=v.get("h8004"),
        )

    def imposta_modalita(self, modalita: Modalita) -> None:
        # shortcut: l'operazione MQTT di scrittura non è ancora stata vista; finché manca, nella catena
        # il comando passa al relè. Serve una registrazione della web app mentre si cambia Comfort/Economy.
        raise ErroreAdattatore("Maxa Connect: scrittura Comfort/Economy non ancora implementata")
