# Segreti

Un file per segreto, permessi 600, mai nel repository (la cartella è in `.gitignore`).

| File | Contenuto | Usato da |
| --- | --- | --- |
| `soliscloud_key_id.txt` | KeyID API SolisCloud | controller |
| `soliscloud_key_secret.txt` | KeySecret API SolisCloud | controller |
| `maxa_utente.txt` | utente Maxa Connect | maxa-bridge |
| `maxa_password.txt` | password Maxa Connect | maxa-bridge |
| `avvisi_token.txt` | token ntfy o bot Telegram (può essere vuoto) | controller |

In modalità simulata bastano file vuoti: `touch secrets/{soliscloud_key_id,soliscloud_key_secret,maxa_utente,maxa_password,avvisi_token}.txt`
