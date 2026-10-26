# Fonti, verifiche e attribuzioni

Questo progetto sta sopra l'**API REST ufficiale** di CHARMM-GUI
(`https://charmm-gui.org/api`, documentazione:
<https://www.charmm-gui.org/?doc=api>). La doc pubblica copre solo `login`,
`check_status`, `download` e `quick_bilayer`: tutto il resto è stato ricavato da
verifiche indipendenti e dal confronto con altri client, **mai** per assunzione.

Ultimo aggiornamento delle verifiche: **24–26 ottobre 2026**.

## 1. Fatti verificati sul campo (nostri)

| Fatto | Come è stato verificato |
|---|---|
| `POST /api/login` con credenziali errate risponde **404** con body testuale `No user or incorrect password!` (non 401, non JSON) | chiamata diretta |
| `GET /api/check_status` senza token risponde **401** `{"error":"No token provided"}` con `Content-Type: text/html` | chiamata diretta |
| `POST /api/quick_bilayer` senza `run_ffconverter=true` restituisce `"modules": "membuilder"` e **nessun input GROMACS** | submit reale, job `9285768142` |
| `check_status` espone anche `rank` ("N job(s) ahead"), `rqinfo`, `hasTarFile`, `lastInpStep`: coda e avanzamento sono leggibili | job reale in coda (19 job davanti) |
| Token scaduto/malformato → **403** `{"error":"Invalid token","detail":"Expired token"}` (non 401), `Content-Type: text/html` | chiamata diretta con token vecchio |
| L'archivio scaricato ha una **coda ASCII** (lunghezza compressa) dopo lo stream gzip | ispezione del file scaricato |
| La coda Redis del server è condivisa: 20 job in coda anche per una membrana 64+64 | `rqinfo` |

## 2. Bug trovato: `Ion_conc` / `Ion_type` maiuscoli sono ignorati

La documentazione ufficiale elenca `Ion_conc` e `Ion_type`. Il server **li
accetta e li ignora**: qualunque valore si mandi, il sistema esce con
**NaCl 0.15 M**. La prova è in `early-step2.2_ions_count.str` di un archivio
generato con `Ion_type=KCl`, `Ion_conc=0.10`: conteneva NaCl 0.15.

I nomi che funzionano sono i **minuscoli** `ion_conc` / `ion_type`
(confermato indipendentemente da `junepark6/cgui_api` e `X0bang/charmm-gui-cli`).
In questo progetto: `Spec.to_form()` manda i minuscoli, e c'è un test che
verifica che i maiuscoli **non** siano presenti nel form.

## 3. Altri repo esaminati (giro su GitHub, 24/10/2026)

Query usate: `charmm-gui` (67 repo), `charmm_gui` (59), `charmmgui` (12),
`charmm-gui api` (3). Quasi tutti i risultati sono workflow GROMACS o script
personali; i soli progetti che parlano davvero con l'API sono questi.

### `junepark6/cgui_api` — toolkit ufficiale (Im Lab), **nessuna licenza**
Bash + Python, login/`quick_bilayer`/`check_status`/`download`, cronologia job in
JSON/SQLite. **Non integrato come codice** (assenza di licenza = nessun diritto di
copia). Usato come **riferimento fattuale** per i nomi dei parametri e per la
conferma che il token è utilizzabile da client terzi.

### `ovcarj/aiida-charmm-gui` — **MIT**, 0★, ultimo push 2026-06-09
Plugin AiiDA (`aiida-charmm-gui` su PyPI) con `client.py` + workflow
`QuickBilayerWorkflow`. **Integrato come idea, non copiato**: separazione
`client`/`workflow`, eccezioni tipizzate, token con scadenza in cache.
Non usiamo AiiDA: sarebbe un database di provenance pesante per un uso
interattivo. Il nostro equivalente leggero è `store.py` (SQLite + `manifest.json`).

### `X0bang/charmm-gui-cli` — **GPL-3.0**, 1★, ultimo push 2026-10-15
CLI cinese per sistemi membrana-proteina-ligando, con un `RESEARCH.md` di
verifiche reali. **Solo fatti, nessun codice** (GPL-3.0 contagierebbe il progetto,
che è MIT). Fatti presi:

- i parametri ioni viaggiano **minuscoli** (vedi §2);
- il **PDB Reader non ha endpoint pubblico**: l'unica via programmatica è il
  login al **form web** (`POST ?doc=sign` con `do=login`, `email`, `password` →
  cookie di sessione) + upload del PDB via HTTP; il `jobid` risultante si passa a
  Quick Bilayer con `clone_job`;
- il 401 può avere `Content-Type: text/html`: non fidarsi del MIME;
- il token è usabile da qualunque client (nessuna firma applicativa).

Nel nostro core il PDB Reader è **fuori scope per scelta**: `Spec` accetta
`clone_job_from` (jobid già esistente) e rifiuta `membrane_only=False` senza di
esso, spiegando perché. L'automazione del form web è un modulo futuro, separato,
perché fragile (dipende dall'HTML del sito, non da un contratto).

### `ChenyanLiao/CHARMM-GUI-System-Builder` — **AGPL-3.0**, skill per agenti
Contiene `docs/API_CAPABILITY_REGISTRY.md`, la ricognizione più onesta che ho
trovato: elenca come **ufficiali** solo login/status/download/quick_bilayer e
marca PDB Reader, Ligand Reader, Membrane Builder e Solution Builder come
**"Audited browser"** (nessun endpoint confermato). **Solo fatti, nessun codice**
(AGPL-3.0). Conferma la nostra scelta di scope.

### `Chris-FIFI/lamellyx` — **MIT**, builder locale
Costruisce bilayer e sistemi membrana-proteina **in locale** per GROMACS, senza
CHARMM-GUI. Non integrato (risolve un altro problema: chi vuole l'output
CHARMM-GUI non lo ottiene da lì). Resta un'alternativa da citare.

### `TiefengSong/Auto-CHARMMGUI-Builder` — automazione browser
Automazione via browser, senza licenza esplicita. **Non integrato.**

### Concorrenti su altri binari (per contesto, non integrati)
- `lolicato/OpenMembraneBuilder` — Streamlit, MARTINI/coarse-grained, AGPL.
- `franciscoadasme/gatewizard-gui` — Electron+Svelte, AmberTools, altra API.
- `madsondeluna/bilbo` — CLI/web FastAPI, bilayer da template PDB.
- `nk53/auto_cgui` — Selenium su quasi tutti i moduli (incluso PDB Reader), RAM
  pesante, max 4 job concorrenti.
- `AmandaStange/CharmmGuiAuto` — YAML → Selenium, solo Firefox.

## 4. Cosa NON facciamo e perché

| Non facciamo | Motivo |
|---|---|
| PDB Reader automatico | nessun endpoint pubblico; il form web è fragile e va isolato |
| Ligand Reader / CGenFF | idem, e serve un file mol2 con parametri |
| Automazione browser (Selenium/Playwright) | dipendenza pesante, fragile, e l'API copre il caso d'uso principale |
| Riscrittura locale del builder lipidico | è esattamente il lavoro che il server fa meglio (e che `lamellyx` fa già) |

## 5. Attribuzione NGL (vendored)

`web/vendor/ngl.js` è una **copia vendORIZED** di [NGL View](https://github.com/nglviewer/ngl)
(nglviewer, Alexander Rose et al.), **MIT**, versione `2.5.0` dalla dist
ufficiale del pacchetto npm `ngl` (UMD standalone con three.js incluso). La
copia è servita localmente da FastAPI/Vite (nessun CDN). Licenza originale in
`web/vendor/NGL-LICENSE`.

## 6. Licenza

`cgui-studio` è **MIT**. Nessun codice GPL/AGPL è stato copiato: i progetti con
licenza copyleft sono citati solo come fonte di fatti verificabili.
