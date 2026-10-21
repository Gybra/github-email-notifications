# github-email-notifications

Notifiche email centralizzate per le repository GitHub, inviate tramite **Brevo SMTP** da un
**reusable workflow** di GitHub Actions.

- Tutta la logica sta in [`.github/workflows/send-notification.yml`](.github/workflows/send-notification.yml).
- Ogni repository cliente contiene solo [`.github/workflows/notify.yml`](.github/workflows/notify.yml) (circa 35 righe, identico per tutte).
- Destinatari, eventi e branch si configurano per repository con le **Variables** di GitHub Actions; le credenziali SMTP con i **Secrets**.
- Nessuna dipendenza: lo script usa solo la libreria standard di Python, già presente sui runner `ubuntu-latest`.

```
repo cliente (privata o pubblica)                 Gybra/github-email-notifications (pubblica)
┌──────────────────────────────┐   workflow_call   ┌───────────────────────────────────────┐
│ .github/workflows/notify.yml │ ────────────────▶ │ .github/workflows/send-notification.yml │ ──▶ smtp-relay.brevo.com:587
│ vars.NOTIFICATION_*          │   inputs + 2      │  (script Python incorporato)            │     STARTTLS
│ secrets.BREVO_SMTP_*         │   secrets         └───────────────────────────────────────┘
└──────────────────────────────┘
```

## Eventi supportati

| Valore in `NOTIFICATION_EVENTS` | Quando                                   | Oggetto email                                       |
|---------------------------------|------------------------------------------|-----------------------------------------------------|
| `push`                          | push su un branch monitorato             | `[GitHub] repo — New push on main` (o `Force push on main`, `Branch main deleted`) |
| `pr_opened`                     | PR aperta verso un branch monitorato     | `[GitHub] repo — Pull Request #42 opened`           |
| `pr_merged`                     | PR mergiata in un branch monitorato      | `[GitHub] repo — Pull Request #42 merged`           |
| `pr_closed`                     | PR chiusa senza merge (opzionale)        | `[GitHub] repo — Pull Request #42 closed`           |
| *(workflow_dispatch)*           | test manuale, ignora i filtri eventi/branch | `[GitHub] repo — Test notification`              |

Default se `NOTIFICATION_EVENTS` è vuota: `push,pr_opened,pr_merged`.

Per le PR, `NOTIFICATION_BRANCHES` si applica al **branch di destinazione** (base): una PR
`feature/x → develop` non genera email se monitori solo `main,master`.

### Niente doppioni al merge

Il merge di una PR genera due eventi: `pull_request_target` (closed, merged) e `push` sul branch
di destinazione. Il run del push interroga l'API GitHub (`GET /repos/{repo}/commits/{sha}/pulls`):
se il commit in testa è il `merge_commit_sha` di una PR mergiata nello stesso branch e `pr_merged`
è attivo, la mail di push viene saltata. Funziona con merge commit, squash e rebase. Se
`pr_merged` è disattivato, il push viene notificato normalmente. Se l'API non risponde, si
preferisce un possibile doppione a una notifica persa.

### Contenuto delle email

HTML responsive (layout a tabelle con stile inline, larghezza massima 600 px) più una versione
testuale. Contiene repository, tipo di evento, branch, autore, data e ora (fuso `Europe/Rome`),
titolo e numero della PR, messaggio dell'ultimo commit, pulsante verso PR, commit o confronto,
e gli ultimi 10 commit. Non include body delle PR, diff né indirizzi email degli autori dei commit.

## 1. Configurare Brevo (una sola volta)

1. **Account**: crea un account su <https://www.brevo.com> (il piano gratuito basta).
2. **Mittente verificato**: *Senders, Domains & Dedicated IPs → Senders → Add a sender*, inserisci
   nome e indirizzo (es. `notifications@tuodominio.it`) e conferma il codice ricevuto via mail.
3. **Dominio autenticato (consigliato)**: *Senders, Domains & Dedicated IPs → Domains → Add a domain*
   e aggiungi i record DNS DKIM e DMARC indicati. Senza autenticazione le mail finiscono facilmente
   in spam. Non usare come mittente un indirizzo `@gmail.com` o `@outlook.com`: le loro policy DMARC
   ne impediscono l'invio da server terzi.
4. **Credenziali SMTP**: menu account → *SMTP & API* → scheda **SMTP**.
   - **Login**: è il valore `xxxxxx@smtp-brevo.com` mostrato lì, **non** l'email dell'account → `BREVO_SMTP_LOGIN`.
   - **Generate a new SMTP key**: copia subito la chiave (`xsmtpsib-...`) → `BREVO_SMTP_KEY`.
     Serve una **SMTP key**, non una API key (`xkeysib-...`).

Parametri usati (fissi nel workflow): host `smtp-relay.brevo.com`, porta `587`, STARTTLS con
verifica del certificato, autenticazione LOGIN.

### Quota Brevo (piano gratuito: 300 email/giorno)

Brevo conta **ogni destinatario** come una email: una notifica a 4 destinatari consuma 4 email.

```
email al giorno ≈ (push + PR aperte + PR mergiate + …) × numero di destinatari, sommato su tutte le repo
```

Esempio: 10 repo con 5 eventi al giorno ciascuna e 3 destinatari → 150 email al giorno. La quota è
condivisa da tutto l'account Brevo, quindi anche da altri usi. Il workflow rifiuta più di 50
destinatari per singola mail. Quando la quota è esaurita Brevo risponde con un errore SMTP e il job
fallisce; l'errore resta visibile nei log.

## 2. Collegare una repository cliente

### 2.1 Caller workflow

Copia [`.github/workflows/notify.yml`](.github/workflows/notify.yml) nella repository cliente,
nello stesso percorso, e fai commit sul **default branch**: `pull_request_target` e
`workflow_dispatch` usano sempre la versione del default branch.

```bash
cd /percorso/repo-cliente
mkdir -p .github/workflows
curl -fsSL https://raw.githubusercontent.com/Gybra/github-email-notifications/v1/.github/workflows/notify.yml \
  -o .github/workflows/notify.yml
git add .github/workflows/notify.yml && git commit -m "ci: add email notifications" && git push
```

### 2.2 Secrets e Variables

*Settings → Secrets and variables → Actions*:

| Tipo     | Nome                    | Obbligatorio | Esempio / default                                     |
|----------|-------------------------|--------------|-------------------------------------------------------|
| Secret   | `BREVO_SMTP_LOGIN`      | sì           | `123abc001@smtp-brevo.com`                            |
| Secret   | `BREVO_SMTP_KEY`        | sì           | `xsmtpsib-…`                                          |
| Variable | `NOTIFICATION_ENABLED`  | sì           | `true` (qualsiasi altro valore = spento)              |
| Variable | `NOTIFICATION_EMAILS`   | sì           | `dev@acme.it,pm@acme.it`                              |
| Variable | `NOTIFICATION_FROM`     | sì           | `GitHub Notifications <notifications@acme.it>`        |
| Variable | `NOTIFICATION_BRANCHES` | no           | default `main,master`; ammessi glob, es. `main,release/*` |
| Variable | `NOTIFICATION_EVENTS`   | no           | default `push,pr_opened,pr_merged`                    |

Le stesse impostazioni via CLI:

```bash
REPO=HexLab-Software/nome-repo
gh secret set BREVO_SMTP_LOGIN --repo "$REPO"        # incolla il valore quando richiesto
gh secret set BREVO_SMTP_KEY   --repo "$REPO"
gh variable set NOTIFICATION_ENABLED --repo "$REPO" --body "true"
gh variable set NOTIFICATION_EMAILS  --repo "$REPO" --body "dev@acme.it,pm@acme.it"
gh variable set NOTIFICATION_FROM    --repo "$REPO" --body "GitHub Notifications <notifications@acme.it>"
# opzionali
gh variable set NOTIFICATION_BRANCHES --repo "$REPO" --body "main,master"
gh variable set NOTIFICATION_EVENTS   --repo "$REPO" --body "push,pr_opened,pr_merged,pr_closed"
```

Vedi anche [`.env.example`](.env.example), solo documentazione: nessun file `.env` viene letto.

**Secrets di organizzazione**: su un piano a pagamento puoi creare `BREVO_SMTP_LOGIN` e
`BREVO_SMTP_KEY` una sola volta (*Organization → Settings → Secrets and variables → Actions*)
e assegnarli alle repo. **Sul piano GitHub Free (come HexLab-Software oggi) secrets e variables
di organizzazione non sono disponibili per le repository private**: in quel caso vanno impostati
per singola repository. Il caller non cambia, perché i secrets di repository hanno la precedenza
su quelli di organizzazione con lo stesso nome.

### 2.3 Policy Actions dell'organizzazione

Se la repo cliente sta in un'organizzazione con policy restrittiva sulle actions, in
*Organization → Settings → Actions → General → Policies* scegli "Allow all actions and reusable
workflows", oppure aggiungi all'allowlist:

```
Gybra/github-email-notifications/.github/workflows/send-notification.yml@*
```

### 2.4 Test manuale

*Actions → Email notifications → Run workflow* (oppure `gh workflow run notify.yml --repo "$REPO"`).
Arriva una mail "Test notification" con la configurazione attiva (eventi, branch, numero di
destinatari) e il link al run. Il test ignora `NOTIFICATION_EVENTS` e `NOTIFICATION_BRANCHES`,
ma rispetta `NOTIFICATION_ENABLED`.

## 3. Configurazione per repository

- **Destinatari diversi per repo**: ogni repo ha la propria `NOTIFICATION_EMAILS`. Separatori ammessi:
  virgola, punto e virgola, spazi o a capo. I duplicati (anche con maiuscole diverse) vengono
  rimossi; gli indirizzi malformati vengono scartati con un warning, e se non ne resta nessuno il
  job fallisce. Tutti i destinatari compaiono nel campo `To` della stessa mail.
- **Attivare o disattivare singoli eventi**: modifica `NOTIFICATION_EVENTS`, per esempio `pr_merged`
  per ricevere solo i merge, o `push,pr_opened,pr_merged,pr_closed` per tutto.
- **Spegnere tutto**: `NOTIFICATION_ENABLED=false`, oppure cancella la variabile. Il job viene
  saltato senza avviare un runner e senza consumare minuti.
- **Branch**: `NOTIFICATION_BRANCHES` è il filtro preciso e accetta glob (`release/*`). GitHub però
  non permette di usare variabili in `on.push.branches`, quindi il caller ha anche un filtro
  grossolano `[main, master]` che evita run inutili. Se monitori altri branch, aggiungili anche lì,
  oppure usa `'**'`, che però avvia un runner a ogni push su qualsiasi branch.
- **Fuso orario**: `Europe/Rome` di default; nel caller aggiungi `timezone: UTC` sotto `with:` per cambiarlo.

## 4. Sicurezza

- **Credenziali**: solo nei GitHub Secrets e passate esplicitamente (`smtp_login`, `smtp_key`),
  senza `secrets: inherit`. GitHub le maschera nei log; lo script non le stampa mai e gli errori SMTP
  mostrano solo codice e messaggio del server.
- **Workflow injection**: nessun dato dell'evento viene interpolato con `${{ }}` dentro uno script.
  Il payload viene letto come JSON da `GITHUB_EVENT_PATH` e gli input arrivano come variabili
  d'ambiente. Un test verifica che lo script incorporato non contenga espressioni `${{`. I messaggi di
  log vengono escapati per impedire l'iniezione di workflow commands (`::...::`).
- **HTML/header injection**: tutto ciò che arriva dall'evento (titoli PR, messaggi di commit, nomi di
  branch) viene escapato con `html.escape`. Oggetto e header sono su una sola riga, senza caratteri di
  controllo. I link vengono mostrati solo se puntano al server GitHub.
- **PR da fork**: il caller usa `pull_request_target`, che ha accesso ai secrets ed esegue sempre la
  versione del workflow presente nel default branch. È sicuro perché **nessun codice della PR viene
  mai fatto checkout o eseguito**: il workflow non usa `actions/checkout`. Non aggiungere step che
  facciano checkout della PR in questo workflow.
- **Permessi minimi**: `contents: read` e `pull-requests: read`, usati solo per la deduplica dei merge
  e per l'elenco dei commit della PR.
- **Affidabilità**: timeout del job di 5 minuti e timeout SMTP di 30 secondi. Gli errori temporanei
  (4xx, disconnessioni, errori di rete) vengono ritentati al massimo 3 volte (attese di 10 e 30
  secondi). Gli errori permanenti (autenticazione 535, mittente rifiutato, 5xx) falliscono subito.
  Un errore sul `QUIT` dopo un invio riuscito non provoca un nuovo invio.
- **Concurrency**: un gruppo per evento distinto (repo + evento + PR/ref + `updated_at`/sha). Le
  riconsegne dello stesso evento vengono serializzate, mentre push o PR diversi non si annullano mai
  a vicenda (`cancel-in-progress: false`).
- **Repo pubblica**: questa repository è pubblica, quindi non deve contenere nessun dato sensibile;
  destinatari e credenziali stanno solo nelle repo clienti. Anche i log dei run di questa repo sono
  pubblici, per questo lo script non stampa mai gli indirizzi email. Il `notify.yml` qui presente
  resta inattivo finché non imposti `NOTIFICATION_ENABLED=true` in questa repo. Se lo attivi,
  chiunque apra una PR può far partire una mail.

## 5. Limitazioni di GitHub Actions da conoscere

- **Reusable workflow e repo private**: un workflow in una repo **privata** è chiamabile solo da repo
  dello stesso owner, dopo averlo abilitato in *Settings → Actions → General → Access*. Per questo
  la repo centrale è **pubblica**: così può essere chiamata da qualsiasi repo, sia di `Gybra` sia di
  `HexLab-Software`, private incluse. Il run avviene nel contesto della repo chiamante, con i suoi
  secrets e i suoi minuti.
- **Niente accesso cross-repo ai file**: il `GITHUB_TOKEN` della repo chiamante non può leggere altre
  repo private. Lo script è incorporato nel workflow, così viaggia con la stessa versione (`@v1`)
  senza checkout né token aggiuntivi.
- **Versioning**: i caller usano `@v1`. Dopo una modifica compatibile sposta il tag
  (`git tag -f v1 && git push -f origin v1`); per modifiche incompatibili crea `v2`. Chi vuole
  l'immutabilità può fissare uno SHA di commit al posto del tag.
- **Variabili nei trigger**: `on.push.branches` non accetta `vars`, da qui il doppio filtro descritto sopra.
- **Eventi generati da `GITHUB_TOKEN`**: push o merge eseguiti da un altro workflow con `GITHUB_TOKEN`
  non avviano nuovi workflow, quindi non generano notifiche. I merge da UI, CLI o con PAT/GitHub App invece sì.
- **Dependabot**: i run avviati da Dependabot non ricevono i secrets di Actions. Se servono notifiche
  sulle sue PR, crea `BREVO_SMTP_LOGIN` e `BREVO_SMTP_KEY` anche come *Dependabot secrets*.
- **Minuti Actions**: sulle repo private ogni job viene arrotondato al minuto (il piano Free include
  2000 minuti al mese); sulle repo pubbliche i minuti sono gratuiti. I job saltati dall'`if` non
  consumano minuti. Ogni push su un branch monitorato e ogni apertura o chiusura di PR consuma circa
  1 minuto, anche quando lo script decide di non inviare (evento disattivato o merge già notificato).
- **Commit elencati**: nella mail compaiono al massimo gli ultimi 10 commit. Per le PR vengono letti i
  primi 100 commit tramite API.

## 6. Diagnostica

Apri il run in *Actions → Email notifications → job "notify / Send email"*. Lo script scrive
annotazioni `notice`, `warning` ed `error` esplicite.

| Sintomo | Causa probabile e soluzione |
|---|---|
| Job **skipped** | `NOTIFICATION_ENABLED` non è `true`. |
| Nessun run al push | Il branch non è in `on.push.branches` del caller, oppure il caller non è sul branch su cui hai fatto push. |
| Nessun run per le PR | `notify.yml` non è sul **default branch** (`pull_request_target` legge solo quello). |
| `Secret smtp_login is required, but not provided` | Il caller non passa i secrets, oppure li passa con nomi diversi. |
| `SMTP credentials are empty` | `BREVO_SMTP_LOGIN` o `BREVO_SMTP_KEY` non esistono nella repo (Free plan: niente secrets di org per le repo private), oppure il run è di Dependabot. |
| `authentication failed (535 …)` | Login sbagliato (serve quello `@smtp-brevo.com`), chiave revocata o API key al posto della SMTP key. Rigenera la SMTP key. |
| `sender refused (…)` | `NOTIFICATION_FROM` non è un mittente verificato su Brevo. |
| `server rejected the message (5xx …)` | Quota giornaliera esaurita, account Brevo sospeso o in revisione: controlla la dashboard di Brevo. |
| `temporary SMTP failure persisted after 3 attempts` | Brevo o la rete non disponibili; rilancia il job con *Re-run*. |
| `cannot connect to smtp-relay.brevo.com:587` | Su runner self-hosted, porta 587 in uscita bloccata dal firewall. |
| `GitHub API returned HTTP 403` | Il caller ha ridotto i `permissions`: servono `contents: read` e `pull-requests: read`. Senza, la deduplica dei merge e l'elenco dei commit PR vengono saltati. |
| `NOTIFICATION_EMAILS is empty or has no valid address` | Variabile mancante o con indirizzi malformati. |
| Workflow non consentito / `not allowed to be used` | Policy Actions dell'organizzazione: vedi §2.3. |
| Mail in spam | Autentica il dominio del mittente su Brevo (DKIM e DMARC). |

## 7. Sviluppo

```bash
python3 -m unittest discover -s tests -v                           # test (solo libreria standard)
mkdir -p /tmp/previews && PREVIEW_DIR=/tmp/previews python3 -m unittest discover -s tests   # salva le email HTML renderizzate
docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest  # lint dei workflow
```

I test estraggono lo script dal blocco `run:` di `send-notification.yml`, quindi verificano
esattamente il codice che gira in CI: parsing della configurazione, filtri, deduplica dei merge,
escaping, retry e gestione degli errori SMTP, con un server SMTP finto. Nessun test invia mail vere.
