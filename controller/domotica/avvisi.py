"""Avvisi verso il telefono: ntfy o bot Telegram. Un solo POST, nessun servizio in più."""
import logging

import requests

log = logging.getLogger(__name__)


class Avvisi:
    def __init__(self, tipo: str = "nessuno", canale: str = "", token: str = ""):
        self.tipo, self.canale, self.token = tipo, canale, token

    def invia(self, testo: str) -> None:
        try:
            if self.tipo == "ntfy" and self.canale:
                intestazioni = {"Authorization": f"Bearer {self.token}"} if self.token else {}
                requests.post(f"https://ntfy.sh/{self.canale}", data=testo.encode(), headers=intestazioni, timeout=10)
            elif self.tipo == "telegram" and self.token and self.canale:
                requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.canale, "text": testo}, timeout=10)
        except requests.RequestException as e:
            log.warning("avviso non inviato: %s", e)
