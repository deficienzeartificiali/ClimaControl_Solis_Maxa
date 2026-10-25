"""Caricamento di config.yaml e dei segreti da /run/secrets."""
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

import yaml

CARTELLA_SEGRETI = Path("/run/secrets")


def _ora(testo: str) -> time:
    ore, minuti = testo.split(":")
    return time(int(ore), int(minuti))


@dataclass(frozen=True)
class RegoleBoost:
    surplus_min_kw: float = 1.5
    soc_min: float = 90
    fascia_inizio: time = time(9, 0)
    fascia_fine: time = time(17, 0)
    pausa_dall_ultimo_cambio_min: int = 20
    prelievo_kw: float = 0.5
    scarica_kw: float = 1.0
    per_min: int = 10
    soc_sotto: float = 70
    dopo_le: time = time(18, 0)
    durata_min_min: int = 30
    cambi_max_giorno: int = 6
    media_campioni: int = 3
    temp_max_c: float | None = None


@dataclass(frozen=True)
class Config:
    solo_osserva: bool = True
    intervallo_s: int = 300
    dati_vecchi_dopo_min: int = 20
    pausa_manuale_ore: float = 4
    boost: RegoleBoost = field(default_factory=RegoleBoost)
    solis: dict = field(default_factory=dict)
    rele: dict = field(default_factory=dict)
    maxa: dict = field(default_factory=dict)
    storico: dict = field(default_factory=dict)
    avvisi: dict = field(default_factory=dict)


def carica(percorso: Path) -> Config:
    dati = yaml.safe_load(percorso.read_text()) or {}
    b = dati.get("boost", {})
    entra, esci = b.get("entra", {}), b.get("esci", {})
    fascia = entra.get("fascia", ["09:00", "17:00"])
    regole = RegoleBoost(
        surplus_min_kw=entra.get("surplus_min_kw", 1.5),
        soc_min=entra.get("soc_min", 90),
        fascia_inizio=_ora(fascia[0]),
        fascia_fine=_ora(fascia[1]),
        pausa_dall_ultimo_cambio_min=entra.get("pausa_dall_ultimo_cambio_min", 20),
        prelievo_kw=esci.get("prelievo_kw", 0.5),
        scarica_kw=esci.get("scarica_kw", 1.0),
        per_min=esci.get("per_min", 10),
        soc_sotto=esci.get("soc_sotto", 70),
        dopo_le=_ora(esci.get("dopo_le", "18:00")),
        durata_min_min=b.get("durata_min_min", 30),
        cambi_max_giorno=b.get("cambi_max_giorno", 6),
        media_campioni=b.get("media_campioni", 3),
        temp_max_c=b.get("temp_max_c"),
    )
    solis = dati.get("solis", {})
    return Config(
        solo_osserva=dati.get("solo_osserva", True),
        intervallo_s=solis.get("intervallo_s", 300),
        dati_vecchi_dopo_min=solis.get("dati_vecchi_dopo_min", 20),
        pausa_manuale_ore=dati.get("pausa_manuale_ore", 4),
        boost=regole,
        solis=solis,
        rele=dati.get("rele", {}),
        maxa=dati.get("maxa", {}),
        storico=dati.get("storico", {}),
        avvisi=dati.get("avvisi", {}),
    )


def segreto(nome: str, cartella: Path = CARTELLA_SEGRETI) -> str:
    """Legge un segreto Docker Compose; stringa vuota se manca (es. in modalità sim)."""
    percorso = cartella / nome
    return percorso.read_text().strip() if percorso.exists() else ""
