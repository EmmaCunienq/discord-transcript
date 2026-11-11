"""Emplacements des fichiers, en mode développement comme dans l'application de bureau.

- RESOURCE_DIR : fichiers livrés avec l'app (page web, bot Discord, Node.js embarqué).
  Dans l'exécutable PyInstaller, c'est le dossier `_internal` (sys._MEIPASS).
- DATA_DIR : données de l'utilisateur (.env, bots et profils, réunions enregistrées, journaux).
  Défini par TRANSCRIPT_DATA_DIR (le lanceur de bureau utilise %LOCALAPPDATA%\\DiscordTranscript),
  sinon la racine du projet comme avant.
"""

import os
import shutil
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", PROJECT_DIR))
DATA_DIR = Path(os.getenv("TRANSCRIPT_DATA_DIR") or PROJECT_DIR)

STATIC_DIR = RESOURCE_DIR / "static"
BOT_DIR = RESOURCE_DIR / "bot"
ENV_FILE = DATA_DIR / ".env"
CONFIG_FILE = DATA_DIR / "config.json"  # bots Discord et profils d'enregistrement
SESSIONS_DIR = DATA_DIR / "output" / "sessions"


def node_executable() -> str | None:
    """Node.js embarqué dans l'application s'il existe, sinon celui du système."""
    bundled = RESOURCE_DIR / "node" / ("node.exe" if os.name == "nt" else "node")
    return str(bundled) if bundled.exists() else shutil.which("node")
