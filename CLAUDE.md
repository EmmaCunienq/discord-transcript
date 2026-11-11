# Discord Transcript

Application de bureau Windows : des bots Discord enregistrent un salon vocal (une piste par participant),
la transcription se fait en local (faster-whisper), le compte rendu est rédigé par Ollama (local, gratuit)
ou Claude, puis publié dans Notion. Interface et textes en **français**.

## Règles du projet

- **Chaque nouvelle fonctionnalité ou changement de comportement arrive avec ses tests**, et la suite complète
  doit passer avant de dire que c'est fini (`python -m pytest`). Si la page change, ajouter/adapter `tests/test_ui.py`.
- **Pas de code mort** : en retirant ou remplaçant une fonctionnalité, supprimer aussi routes, fonctions, styles,
  icônes SVG, variables d'environnement et sections du README devenues inutiles. `tests/test_code_quality.py`
  en vérifie une partie (pyflakes, icônes inutilisées, ids manquants).
- **Pas de service payant par défaut** : le résumé passe par Ollama (`OLLAMA_MODEL`) tant que l'app est locale.
  Un déploiement sur un site est prévu (d'où `TRANSCRIPT_ACCESS_KEY` et le serveur utilisable sans l'app de bureau).
- Mettre à jour le README quand le comportement visible change.
- Ne pas faire rejoindre un vrai salon au bot ni créer de vraie page Notion pour tester : utiliser les tests
  (tout est simulé) ou un serveur isolé avec `TRANSCRIPT_DATA_DIR` temporaire.

## Commandes

```powershell
.venv-win\Scripts\python.exe desktop\main.py                 # lancer l'app de bureau (dev)
.venv-win\Scripts\python.exe -m pytest                       # tous les tests (~40 s, Edge + Whisper)
.venv-win\Scripts\python.exe -m pytest --skip-slow           # tests rapides (~4 s)
powershell -ExecutionPolicy Bypass -File desktop\build.ps1   # tests rapides puis .exe dans dist\DiscordTranscript\
cd bot; npm install                                          # dépendances du bot
```

Environnement Python : `.venv-win` (Python 3.12). `.venv` est un ancien environnement macOS, ne pas l'utiliser.

## Architecture

| Chemin | Rôle |
|---|---|
| `desktop/main.py` | Lanceur : fenêtre pywebview (WebView2), serveur uvicorn dans un thread (port fixe 8765), menu, API JS des réglages (`window.pywebview.api`), redémarrage |
| `app/server.py` | FastAPI : page, API des profils / réunions / bots / Notion, routes appelées par les bots |
| `app/config.py` | `config.json` : bots (nom + token) et profils (bot, salon, page Notion, contexte) ; migration depuis l'ancien `.env` |
| `app/bots.py` | Un processus Node par bot, port de contrôle libre, `call(bot_id, path, payload)` |
| `app/discord_sessions.py` | Réunions : file de transcription, fin de réunion (résumé + Notion), corrections, sauvegarde et rechargement depuis `output/sessions/<id>/` |
| `app/transcriber.py` | faster-whisper (CPU, int8), ré-échantillonnage |
| `app/summarizer.py` | Prompt du compte rendu, Ollama ou Claude en flux |
| `app/local_ai.py` | Ollama géré par l'app : utilise un Ollama ouvert, sinon lance l'installé, sinon télécharge la version Windows dans `DATA_DIR/ollama` (vérifiée par SHA-256) ; télécharge le modèle ; arrête seulement l'Ollama qu'il a lancé |
| `app/notion.py` | Recherche de pages, conversion Markdown → blocs, publication |
| `app/envfile.py` | Lecture du `.env` (serveur et lanceur) ; `child_env` : environnement des bots et d'Ollama, sans les clés de l'app |
| `app/paths.py` | `RESOURCE_DIR` (fichiers livrés, `_MEIPASS` dans l'exe) et `DATA_DIR` (`TRANSCRIPT_DATA_DIR`, `%LOCALAPPDATA%\DiscordTranscript` dans l'app) |
| `bot/index.js` | Bot discord.js : rejoint le salon, envoie une piste par participant, API de contrôle locale (`/guilds`, `/channels`, `/record`, `/stop`) |
| `static/index.html` | Page unique sans dépendance : onglets de profils, enregistrement, transcription, résumé, historique du profil |
| `static/settings.html` | Réglages (bots, jeton Notion, Ollama/Claude, Whisper) — uniquement dans l'app de bureau |
| `desktop/DiscordTranscript.spec` | PyInstaller (onedir) : embarque `static/`, `bot/` avec `node_modules` et `node.exe` |

Données utilisateur (`DATA_DIR`) : `.env` (clés), `config.json` (bots, profils ; copie de secours `config.backup.json`), `output/sessions/`, `logs/app.log`,
`webview/` (stockage de la fenêtre). Les variables d'environnement sont lues **à l'import** des modules.

## Pièges connus

- **Windows / mimetypes** : `mimetypes` lit le registre au premier fichier servi (des dizaines de secondes) ;
  désactivé dans `server.py`, ne pas retirer. La table intégrée ne connaît pas `.webp` : ajouté à la main.
- **Hôte local** : sans `TRANSCRIPT_ACCESS_KEY`, le serveur refuse les noms d'hôte non locaux et les POST
  d'une autre origine. Dans les tests, `TestClient(..., base_url="http://127.0.0.1:8765")`.
- **Bots** : l'API de contrôle exige l'en-tête `X-Control-Key` (secret tiré par `bots.start_all`).
- **Fonds des thèmes** : WebP dans `static/assets/` (+ vignettes `*-preview.webp`) ; PNG d'origine dans
  `output/design/originals/`, non embarqués.
- **Cache WebView2** : la page est servie avec `Cache-Control: no-cache`, sinon la fenêtre garde d'anciennes versions.
- **pywebview js_api** : tout attribut public de `Api` est parcouru récursivement ; garder la fenêtre dans `_window`.
- **Page** : les éléments à `id` sont des globales JS ; une fonction de même nom les masque (ex. `profileSummary`).
  Dans les tests UI, préférer `document.getElementById(...)`.
- **Port fermé sous Windows** : une connexion refusée met ~2 s ; `bots.call` vérifie d'abord si le processus vit.
- **Ollama** : ne jamais arrêter l'application Ollama de l'utilisateur (seulement `local_ai._proc`). Premier
  téléchargement : 1,6 Go + ~7 Go de modèle, d'où la vérification d'espace disque (12 Go).
- **Discord** : un bot ne peut être que dans un salon vocal à la fois par serveur ; pour deux salons en même temps,
  deux bots.
- **PowerShell 5.1** lit les `.ps1` sans BOM en ANSI : garder `build.ps1` en UTF-8 avec BOM.
- Tests manuels avec `curl` depuis Git Bash : les accents dans `-d '...'` sont mal encodés ; utiliser de l'ASCII.
