# Discord Transcript

Widget local pour **enregistrer une réunion Discord, la transcrire en direct et en générer un compte rendu**.
Un bot enregistre chaque participant sur sa propre piste : la transcription indique qui dit quoi, y compris
quand plusieurs personnes parlent en même temps.

- **Transcription** : [faster-whisper](https://github.com/SYSTRAN/faster-whisper), exécuté en local — l'audio ne quitte jamais la machine.
- **Résumé** : Claude (API Anthropic, seul le texte de la transcription est envoyé) ou un modèle local via Ollama (`OLLAMA_MODEL`).
- **Interface** : une page web servie en local (aucune dépendance front, fonctionne hors ligne pour la partie transcription).
- **Apparence** : dans **••• → Réglages → Apparence**, choisir **Basique** (épuré, sans décor, par défaut),
  **Forêt** (le design d'origine), **Ville** (toits au crépuscule), **Montagne** (sommets et lac alpin),
  **Mer** (côte méditerranéenne) ou **Jardin japonais** (cerisiers et lanternes).
  Les ambiances artistiques associent une illustration locale, une palette, une typographie et des ornements.
  Basique et Forêt suivent le mode clair/sombre du système. Les aperçus montrent les vrais décors ; le choix
  s'applique immédiatement aux deux pages et se mémorise sur cet appareil, sans redémarrage.
  Disponible aussi dans le navigateur et commun à tous les profils. Les anciens choix Papier, Minuit et
  Aurore deviennent respectivement Basique, Ville et Mer.
  Le bouton **•••**, intégré en haut de chaque page, ouvre le menu assorti au thème : Accueil, Réglages,
  et dans l'application de bureau, Dossier des réunions, Ouvrir le journal et Quitter l'application.
  Il remplace la barre de menu Windows. Sous Windows 11, le titre et la bordure restent assortis à la palette active.
  Le logo est un micro dans un carré arrondi : son dessin est identique pour toutes les ambiances,
  ses couleurs suivent le thème, dans la page, l'onglet du navigateur et l'icône de la fenêtre Windows.
  L'application possède aussi sa propre identité dans la barre des tâches, même en développement avec Python,
  pour afficher le micro et regrouper ses fenêtres sous Discord Transcript.
  Les barres de défilement de la page, des transcriptions et de l'historique suivent aussi le thème,
  avec un curseur arrondi et une couleur de survol assortie.

## Installation

Prérequis : Python ≥ 3.10 (ex. `brew install python@3.12`).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # ou `ant auth login`
```

## Lancement

```bash
.venv/bin/uvicorn app.server:app --port 8010
```

Puis ouvrir <http://localhost:8010>. Au premier lancement, le modèle Whisper (~500 Mo pour `small`) est téléchargé.

## Application de bureau (Windows)

Une fenêtre native, sans navigateur ni terminal : le serveur de transcription et les bots Discord tournent
en arrière-plan et s'arrêtent à la fermeture de la fenêtre. Node.js est embarqué, rien d'autre à installer.

```powershell
powershell -ExecutionPolicy Bypass -File desktop\build.ps1
```

L'application est créée dans `dist\DiscordTranscript\` : lancer `DiscordTranscript.exe`. Si Inno Setup 6 est installé
(`winget install JRSoftware.InnoSetup`), le build produit aussi l'installeur `dist\DiscordTranscript-Setup-<version>.exe`
(version dans `app/__init__.py`) : installation sans droits administrateur dans
`%LOCALAPPDATA%\Programs\Discord Transcript`, raccourci dans le menu Démarrer, mise à jour en relançant un installeur
plus récent. Désinstaller (Paramètres Windows → Applications) garde les réglages et les réunions.
Pour la tester sans la construire : `.venv-win\Scripts\python.exe desktop\main.py`.

- **Réglages** (menu **••• → Réglages**, en haut de la page) : les **bots Discord**
  (nom + token, autant que tu veux), le jeton Notion, la clé Claude ou le modèle Ollama, le modèle Whisper.
  Enregistrer redémarre l'application. Les réglages sont stockés dans `%LOCALAPPDATA%\DiscordTranscript\`
  (`.env`, et `config.json` pour les bots et les profils), avec les réunions (`output\sessions\`) et le journal
  (`logs\app.log`). En développement, le `.env` du projet y est copié au premier lancement.
- Au premier lancement, le modèle Whisper est téléchargé (~500 Mo pour `small`).
- **Résumé local (Ollama)** : par défaut, l'application rédige les comptes rendus avec Ollama et `mistral-nemo`,
  gratuitement et sans rien envoyer en ligne. Elle s'en occupe seule : si Ollama est déjà ouvert, elle l'utilise ;
  s'il est installé, elle le lance ; sinon elle le télécharge (1,6 Go, dans `%LOCALAPPDATA%\DiscordTranscript\ollama`),
  puis télécharge le modèle s'il manque (~7 Go). Prévoir **12 Go libres** la première fois ; un bandeau montre la
  progression, et un résumé demandé pendant ce temps attend qu'Ollama soit prêt. À la fermeture, l'application
  arrête l'Ollama qu'elle a lancé (jamais l'application Ollama ouverte à part).
- L'application utilise le port local 8765 (et un port libre par bot). Ne pas la lancer en même temps que
  le serveur `uvicorn` avec les mêmes bots : ils seraient connectés deux fois.

## Réunions Discord

Le bot rejoint le salon vocal et reçoit **une piste audio par participant** (Discord les transmet séparément).
Chaque prise de parole est transcrite avec le nom de son auteur, puis replacée dans l'ordre chronologique :

```
[00:01] Marc : Je propose qu'on valide le budget aujourd'hui, et Julie prépare la présentation.
[00:04] Julie : D'accord, je l'envoie jeudi soir.
```

L'audio va directement de Discord à ton Mac (chiffrement de bout en bout DAVE géré) : rien ne passe par un service tiers.

### Mise en place (une seule fois)

Prérequis : Node.js ≥ 22 (`brew install node`).

1. Sur <https://discord.com/developers/applications> : **New Application**, puis onglet **Bot** → **Reset Token** et copie le token.
   Aucun « Privileged Gateway Intent » n'est nécessaire.
2. Ajoute-le dans **••• → Réglages → Bots Discord** (nom + token). Sans l'application de bureau : crée un fichier
   `.env` à la racine du projet (ignoré par git) avec `DISCORD_TOKEN=le-token-du-bot` ; au premier lancement,
   il est repris comme premier bot dans `config.json` (avec un premier profil).
3. Installe le bot : `cd bot && npm install`
4. Invite le bot sur ton serveur (remplace `APPLICATION_ID`, visible dans l'onglet **General Information**) :
   `https://discord.com/oauth2/authorize?client_id=APPLICATION_ID&scope=bot+applications.commands&permissions=1051648`
   (permissions : voir les salons, envoyer des messages, se connecter).

Chaque bot configuré démarre avec le serveur. Pour enregistrer deux salons d'un même serveur en même temps,
crée un deuxième bot (une autre application Discord) et invite-le aussi.

### Profils (onglets)

Chaque **onglet** est un profil d'enregistrement : un bot, un salon vocal, une page Notion et un contexte par
défaut (sujet, participants… pour aider le résumé). **+** crée un profil, **✎ Modifier** le règle (listes de
choix : salons vus par le bot, pages Notion partagées avec l'intégration).

1. Choisis l'onglet, puis **● Enregistrer** : le bot du profil rejoint son salon et annonce l'enregistrement dans
   son chat. La transcription s'affiche en direct, avec le nom de chaque intervenant ; sous le bouton, le nombre
   de participants présents dans le salon et d'intervenants déjà transcrits.
2. **Terminer** fait quitter le salon au bot (il part aussi seul si le salon reste vide une minute).
   Le compte rendu est alors rédigé automatiquement (En bref, Points abordés, Décisions, Actions à mener,
   Questions ouvertes) et publié sur la page Notion du profil.
3. Si besoin, corrige la transcription (enregistré automatiquement) puis **Refaire le résumé**. **Copier** ou
   **Télécharger .md** (compte rendu + transcription complète).

Sous l'enregistrement, chaque onglet liste les **réunions de ce profil** : un clic pour les revoir. Les onglets
de profils qui enregistrent ont un point rouge ; on peut passer de l'un à l'autre à tout moment. Un bot ne peut
être que dans un salon vocal à la fois par serveur Discord.

En secours, les commandes `/enregistrer` et `/stop` font la même chose depuis Discord (la réunion est rangée
dans le profil du même bot et du même salon).

Chaque réunion est enregistrée dans `output/sessions/<date>/` (`transcript.md`, `resume.md` et `transcript.json`,
qui garde aussi les corrections, le contexte et le lien Notion) et rechargée au démarrage.
Pense à prévenir les participants qu'ils sont enregistrés.

## Tests

```powershell
.venv-win\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv-win\Scripts\python.exe -m pytest              # tout (~1 min)
.venv-win\Scripts\python.exe -m pytest --skip-slow  # sans Whisper ni interface (quelques secondes)
```

Les tests tournent dans un dossier de données temporaire : ils ne touchent ni à tes réglages ni à tes réunions.
Discord, Notion et le modèle de résumé sont simulés (aucun salon rejoint, aucune page créée).

| Fichier | Ce qui est vérifié |
|---|---|
| `test_config.py` | bots et profils, `config.json` illisible ou incomplet, reprise des anciens réglages, profil d'une réunion lancée par `/enregistrer` |
| `test_sessions.py` | transcription au fil de l'eau, fin de réunion, résumé, Notion, corrections, sauvegardes simultanées, rechargement depuis le disque |
| `test_server.py` | toutes les routes de l'API (profils, réunions, bots, Notion, clé d'accès, refus des autres sites et des noms d'hôte non locaux, audio invalide) |
| `test_bots.py` | processus des bots (un par token), secret de contrôle par bot, aucune clé transmise inutilement, erreurs de bot arrêté ou absent |
| `test_notion.py` | liens et ID de pages, conversion en blocs, recherche de pages, publication |
| `test_summarizer.py` | résumé par Ollama (flux, erreurs, transcription trop longue) |
| `test_local_ai.py` | Ollama géré par l'app : déjà ouvert, installé, téléchargé, modèle manquant, disque plein, somme de contrôle, archive corrompue, arrêt |
| `test_desktop_settings.py` | réglages de l'application de bureau (`.env`, bots, redémarrage) |
| `test_transcriber.py` | ré-échantillonnage et **vraie transcription Whisper** d'une phrase enregistrée (lent) |
| `test_ui.py` | **la page dans Edge sans fenêtre** : onglets, enregistrement, Terminer, résumé, correction, réglage et création de profils, historique (lent) |
| `test_installer.py` | installeur cohérent avec le build (dossier, exe, icône), installé sans admin hors du dossier des données |
| `test_code_quality.py` | pas d'import ni d'icône inutilisés, scripts de la page et du bot valides |

`desktop\build.ps1` lance les tests rapides avant de construire l'application et s'arrête si l'un échoue.
Chaque nouvelle fonctionnalité doit arriver avec ses tests.

## Configuration

| Variable | Défaut | Rôle |
|---|---|---|
| `WHISPER_MODEL` | `small` | `base` (plus rapide), `medium` / `large-v3` (plus précis, plus lent) |
| `WHISPER_LANGUAGE` | `fr` | Langue de la réunion ; vide = détection automatique |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Modèle Claude utilisé pour le résumé |
| `OLLAMA_MODEL` | — (`mistral-nemo` dans l'app de bureau) | Si défini, le résumé est rédigé en local et gratuitement par [Ollama](https://ollama.com) au lieu de Claude ; Ollama et le modèle sont téléchargés et lancés automatiquement |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Adresse d'Ollama |
| `OLLAMA_NUM_CTX` | `32768` | Taille de contexte du modèle local (limite la longueur d'une transcription) |
| `DISCORD_TOKEN` | — | Premier lancement uniquement : repris comme premier bot dans `config.json` |
| `NOTION_TOKEN` | — | Jeton de l'intégration Notion, pour publier le compte rendu |
| `TRANSCRIPT_ACCESS_KEY` | — | Clé d'accès obligatoire dès que l'app est publiée (voir ci-dessous) |

Ces variables peuvent être définies dans l'environnement ou dans le fichier `.env`. Les bots et les profils
sont dans `config.json` (dossier de données), modifiables depuis l'application. Une copie de secours
(`config.backup.json`) est tenue à jour : si `config.json` devient illisible (modifié à la main, disque plein…),
il est mis de côté dans `config.broken.json` et l'app reprend la copie de secours.

## Sécurité

- **App locale (sans `TRANSCRIPT_ACCESS_KEY`)** : le serveur n'accepte que les adresses `127.0.0.1`, `localhost`
  et `[::1]`, et refuse tout envoi venant d'un autre site (en-tête `Origin`). Une page web ouverte dans le
  navigateur ne peut donc ni lancer un enregistrement, ni publier dans Notion, ni lire les transcriptions.
- **Bots** : chaque bot reçoit à son lancement un secret aléatoire, exigé par son API de contrôle locale ;
  il ne reçoit que son propre token Discord (pas les clés Notion ou Anthropic). Ollama non plus.
- **Téléchargement d'Ollama** : l'archive est vérifiée avec la somme SHA-256 publiée avec la même version
  (`sha256sum.txt` de la release GitHub) ; en cas d'écart, rien n'est installé.
- **App publiée** : la clé d'accès protège toutes les routes (voir ci-dessous).

## Intégration dans Notion

Une fois l'app publiée sur une adresse HTTPS, définis `TRANSCRIPT_ACCESS_KEY` (une longue chaîne aléatoire, par
exemple `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`) : sans elle, n'importe qui trouvant
l'adresse pourrait lancer un enregistrement ou lire les transcriptions. Puis, dans Notion, ajoute un bloc
**/embed** avec l'adresse :

```
https://ton-domaine/?key=TA_CLE
```

### Compte rendu dans une nouvelle page

À la fin de la réunion, une **nouvelle page** est créée sous la page Notion du profil :

> 🎙️ **Réunion du 15 octobre 2026** *(titre de la page)*
> ▸ Transcription *(titre déroulant, avec la transcription complète)*
> En bref · Points abordés · Décisions · Actions à mener *(cases à cocher)* · Questions ouvertes

Mise en place (une fois) :

1. Sur <https://www.notion.so/profile/integrations>, crée une intégration **interne** avec les capacités
   « Lire le contenu » et « Insérer du contenu », puis copie son jeton dans `.env` : `NOTION_TOKEN=ntn_...`
2. Sur chaque page Notion qui doit recevoir des comptes rendus : menu **•••** → **Connexions** → ajoute
   l'intégration.
3. Choisis cette page dans le profil (**✎ Modifier** → Page Notion des comptes rendus).

C'est le serveur qui rédige le résumé et crée la page dès que la dernière prise de parole est transcrite :
ça fonctionne même si plus personne n'a la page ouverte, sans doublon si plusieurs personnes l'ont ouverte.
Le bouton **Notion** de l'onglet Résumé permet de renvoyer un compte rendu corrigé à la main (il crée une
nouvelle page).
