# ClimaControl Solis + Maxa

Servizio h24 che usa il surplus del fotovoltaico Solis per mandare in boost la pompa di calore Maxa,
e la riporta a Normale quando il surplus finisce. Gira in Docker Compose su una macchina Linux con 2 GB di RAM.

Architettura completa: [documento di architettura](https://claude.ai/artifact/VgohFUTT4aySbTbRXH4ffs).

## Struttura

| Cartella | Contenuto |
| --- | --- |
| `controller/` | logica a stati, scheduler, adattatori, storico SQLite, pagina di stato `:8080` |
| `simulatore/` | stub minimo per avviare lo stack con dati finti (profilo `sim`) |
| `config/` | `config.example.yaml` con soglie e fasce orarie |
| `secrets/` | un file per segreto, fuori da git |

Gli adattatori si scelgono da `.env`: `FONTE_SOLARE=soliscloud|sim`, `POMPA=maxa_cloud|maxa_cloud,rele|rele|sim`
(la pompa usa una catena: se il primo adattatore fallisce si passa al successivo),
`SENSORI_TEMP=openwebnet|sim|nessuno` per la temperatura di casa dal termostato BTicino MyHOME.

## Avvio

```sh
cp .env.example .env              # oppure .env.sim.example -> .env.sim
cp config/config.example.yaml config/config.yaml
touch secrets/{soliscloud_key_id,soliscloud_key_secret,maxa_utente,maxa_password,avvisi_token,own_password}.txt
chmod 600 secrets/*.txt

docker compose --env-file .env.sim --profile sim up -d   # con il simulatore
docker compose up -d                                      # impianto reale
docker compose logs -f controller
```

Il controller parte con `solo_osserva: true`: calcola e registra le decisioni senza comandare la Maxa.

`.env` (o il file passato con `--env-file`) sceglie gli adattatori; `config/config.yaml` le soglie,
e viene riletto da solo quando cambia (se non è valido resta in uso quello precedente).
Stato, problemi aperti e ultime decisioni: `http://<pc>:8080/stato` (JSON). Pausa manuale:
`curl -X POST 'http://<pc>:8080/pausa?ore=4'`.

Docker segna il controller `unhealthy` se il ciclo non gira da oltre 700 s, ma non lo riavvia da solo:
si vede con `docker ps`. Un avviso parte quando un problema inizia (dati Solis assenti, comando Maxa
fallito) e uno quando si risolve.

## Test

```sh
cd controller
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

## Da completare

- SolisCloud: verificare nomi dei campi e segni di `psum` e `batteryPower` sulla prima risposta reale.
- Maxa Connect (`maxa_cloud`): login e lettura dal cloud come la web app (Cognito, AWS IoT MQTT).
  Manca il comando Comfort/Economy: serve una registrazione della web app mentre lo si cambia.
  Le letture della pompa (modalità, mandata, esterna, potenza assorbita) finiscono nella tabella `pompa`
  dello storico, utile per tarare `surplus_min_kw`.
- Relè: scegliere l'ingresso Maxa (X-15 o X-20) dal manuale installatore, IP dello Shelly in `config.yaml`,
  stato all'accensione dello Shelly su "spento" (dopo un blackout la Maxa torna in Normale).
- Limite di immissione: se l'inverter taglia la cessione, il surplus non si vede da `psum` e il boost non parte.
- Estate: la regola `temp_max_boost` vale solo per il riscaldamento.
- Sensori BTicino: verificare sul gateway reale il tipo di autenticazione OpenWebNet e gli indirizzi zona. In fase 1 si usa una sola temperatura "casa" (media delle zone trovate), in sola lettura.
- Simulatore completo (nuvole, scenari forzati, batteria realistica).
