"""Costruzione degli adattatori a partire da .env e config.yaml."""
import os

from ..config import Config, segreto
from .base import ErroreAdattatore, FonteSolare, PompaDiCalore, SensoriTemperatura
from .catena import CatenaPompa

__all__ = ["ErroreAdattatore", "FonteSolare", "PompaDiCalore", "SensoriTemperatura", "crea_fonte_solare", "crea_pompa",
           "crea_sensori"]


def crea_fonte_solare(cfg: Config) -> FonteSolare:
    tipo = os.environ.get("FONTE_SOLARE", "sim")
    if tipo == "soliscloud":
        from .soliscloud import SolisCloud
        return SolisCloud(
            key_id=segreto("soliscloud_key_id"),
            key_secret=segreto("soliscloud_key_secret"),
            inverter_sn=str(cfg.solis.get("inverter_sn", "")),
            cessione_positiva=cfg.solis.get("rete_cessione_positiva", True),
            carica_positiva=cfg.solis.get("batteria_carica_positiva", True),
        )
    if tipo == "sim":
        from .sim import SolareSim
        return SolareSim(os.environ.get("SIMULATORE_URL", "http://simulatore:8070"))
    raise ValueError(f"FONTE_SOLARE sconosciuta: {tipo}")


def crea_pompa(cfg: Config) -> PompaDiCalore:
    anelli = []
    for tipo in os.environ.get("POMPA", "sim").split(","):
        tipo = tipo.strip()
        if tipo == "maxa_web":
            from .maxa_web import MaxaWeb
            anelli.append(MaxaWeb(os.environ.get("MAXA_BRIDGE_URL", "http://maxa-bridge:8090")))
        elif tipo == "rele":
            from .rele_shelly import ReleShelly
            anelli.append(ReleShelly(str(cfg.rele.get("ip", "")), int(cfg.rele.get("canale", 0))))
        elif tipo == "sim":
            from .sim import PompaSim
            anelli.append(PompaSim(os.environ.get("SIMULATORE_URL", "http://simulatore:8070")))
        else:
            raise ValueError(f"POMPA: adattatore sconosciuto {tipo}")
    return CatenaPompa(anelli, cfg.maxa.get("errori_prima_di_sospendere", 3), cfg.maxa.get("sospensione_min", 60))


def crea_sensori(cfg: Config) -> SensoriTemperatura:
    tipo = os.environ.get("SENSORI_TEMP", "nessuno")
    if tipo == "openwebnet":
        from .openwebnet import SensoriOpenWebNet
        zone = cfg.sensori.get("zone", "auto")
        return SensoriOpenWebNet(
            host=os.environ.get("OWN_HOST", ""),
            porta=int(os.environ.get("OWN_PORT", "20000")),
            password=segreto("own_password"),
            nomi=zone if isinstance(zone, dict) else None,
        )
    if tipo == "sim":
        from .sim import SensoriSim
        return SensoriSim(os.environ.get("SIMULATORE_URL", "http://simulatore:8070"))
    if tipo == "nessuno":
        from .openwebnet import SensoriNessuno
        return SensoriNessuno()
    raise ValueError(f"SENSORI_TEMP sconosciuto: {tipo}")
