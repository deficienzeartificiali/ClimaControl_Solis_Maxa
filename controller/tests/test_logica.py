from datetime import datetime, timedelta, timezone

import pytest

from domotica.config import Config
from domotica.logica import Logica
from domotica.modelli import Campione, Modalita, StatoPompa

TZ = timezone(timedelta(hours=1))
GIORNO = datetime(2026, 11, 3, 12, 0, tzinfo=TZ)


def serie(fine: datetime, n: int, **valori) -> list[Campione]:
    base = dict(pv_kw=4.0, carico_kw=0.5, rete_kw=-2.0, batteria_kw=0.5, soc=95)
    base.update(valori)
    return [Campione(ts=fine - timedelta(minutes=5 * (n - 1 - i)), **base) for i in range(n)]


@pytest.fixture
def logica():
    return Logica(Config())


def in_boost(logica: Logica, da: datetime) -> Logica:
    logica.registra_cambio(Modalita.BOOST, da)
    return logica


def test_entra_in_boost_con_surplus_e_batteria_piena(logica):
    d = logica.decidi(serie(GIORNO, 3), GIORNO)
    assert d.cambia and d.modalita == Modalita.BOOST


def test_resta_normale_se_batteria_non_abbastanza_carica(logica):
    d = logica.decidi(serie(GIORNO, 3, soc=80), GIORNO)
    assert not d.cambia and d.modalita == Modalita.NORMALE


def test_carica_batteria_conta_come_surplus_solo_sopra_soglia_soc(logica):
    sotto = Campione(GIORNO, 4, 0.5, 0.0, 3.0, 85)
    sopra = Campione(GIORNO, 4, 0.5, 0.0, 3.0, 92)
    assert logica.surplus_kw(sotto) == 0
    assert logica.surplus_kw(sopra) == 3.0


def test_servono_tre_campioni_per_la_media(logica):
    assert not logica.decidi(serie(GIORNO, 2), GIORNO).cambia


def test_niente_boost_fuori_fascia(logica):
    sera = GIORNO.replace(hour=17, minute=30)
    assert not logica.decidi(serie(sera, 3), sera).cambia


def test_niente_boost_con_dati_vecchi(logica):
    assert not logica.decidi(serie(GIORNO - timedelta(minutes=30), 3), GIORNO).cambia


def test_dati_vecchi_in_boost_tornano_a_normale(logica):
    in_boost(logica, GIORNO - timedelta(minutes=5))
    d = logica.decidi(serie(GIORNO - timedelta(minutes=30), 3), GIORNO)
    assert d.cambia and d.modalita == Modalita.NORMALE


def test_durata_minima_blocca_uscita_per_prelievo(logica):
    in_boost(logica, GIORNO - timedelta(minutes=15))
    d = logica.decidi(serie(GIORNO, 3, rete_kw=1.0, soc=92), GIORNO)
    assert not d.cambia


def test_esce_per_prelievo_sostenuto_dopo_durata_minima(logica):
    in_boost(logica, GIORNO - timedelta(minutes=45))
    d = logica.decidi(serie(GIORNO, 3, rete_kw=1.0, soc=92), GIORNO)
    assert d.cambia and d.modalita == Modalita.NORMALE


def test_un_solo_campione_di_prelievo_non_basta(logica):
    in_boost(logica, GIORNO - timedelta(minutes=45))
    campioni = serie(GIORNO - timedelta(minutes=5), 2, soc=92) + [Campione(GIORNO, 1, 2, 1.0, 0, 92)]
    assert not logica.decidi(campioni, GIORNO).cambia


def test_soc_basso_esce_anche_prima_della_durata_minima(logica):
    in_boost(logica, GIORNO - timedelta(minutes=5))
    d = logica.decidi(serie(GIORNO, 3, soc=65), GIORNO)
    assert d.cambia and d.modalita == Modalita.NORMALE


def test_esce_dopo_le_diciotto(logica):
    sera = GIORNO.replace(hour=18, minute=5)
    in_boost(logica, sera - timedelta(minutes=10))
    d = logica.decidi(serie(sera, 3), sera)
    assert d.cambia and d.modalita == Modalita.NORMALE


def test_massimo_cambi_giornalieri(logica):
    for i in range(6):
        logica.registra_cambio(Modalita.NORMALE, GIORNO - timedelta(hours=3, minutes=i))
    assert not logica.decidi(serie(GIORNO, 3), GIORNO).cambia


def test_pausa_dopo_ultimo_cambio(logica):
    logica.registra_cambio(Modalita.NORMALE, GIORNO - timedelta(minutes=10))
    assert not logica.decidi(serie(GIORNO, 3), GIORNO).cambia


def test_cambio_manuale_dall_app_mette_in_pausa(logica):
    pompa = StatoPompa(ts=GIORNO, modalita=Modalita.BOOST, accesa=True)
    d = logica.decidi(serie(GIORNO, 3, rete_kw=1.0), GIORNO, pompa)
    assert not d.cambia
    dopo = GIORNO + timedelta(hours=1)
    assert "pausa" in logica.decidi(serie(dopo, 3, soc=50), dopo).motivo


def test_stato_letto_subito_dopo_nostro_comando_non_e_manuale(logica):
    logica.registra_cambio(Modalita.BOOST, GIORNO - timedelta(minutes=2))
    pompa = StatoPompa(ts=GIORNO, modalita=Modalita.NORMALE, accesa=True)
    d = logica.decidi(serie(GIORNO, 3), GIORNO, pompa)
    assert "app" not in d.motivo


def test_comfort_massimo_impedisce_boost():
    from domotica.config import RegoleBoost
    logica = Logica(Config(boost=RegoleBoost(temp_max_c=23)))
    pompa = StatoPompa(ts=GIORNO - timedelta(hours=1), modalita=Modalita.NORMALE, accesa=True, temp_ambiente_c=23.5)
    assert not logica.decidi(serie(GIORNO, 3), GIORNO, pompa).cambia
