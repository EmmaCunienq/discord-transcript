"""Isolation des tests : dossier de données temporaire, aucun vrai bot, Notion ni modèle de résumé.

Les modules de l'app lisent leur configuration à l'import : l'environnement est donc fixé ici,
avant tout import de `app`.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(tempfile.mkdtemp(prefix="dt-tests-"))

os.environ["TRANSCRIPT_DATA_DIR"] = str(DATA_DIR)
for key in ("DISCORD_TOKEN", "DISCORD_GUILD_ID", "NOTION_TOKEN", "NOTION_PAGE_ID", "ANTHROPIC_API_KEY",
            "OLLAMA_MODEL", "TRANSCRIPT_ACCESS_KEY", "WHISPER_LANGUAGE"):
    os.environ.pop(key, None)
sys.path.insert(0, str(ROOT))

from app import config, discord_sessions  # noqa: E402
from app.paths import CONFIG_FILE, ENV_FILE, SESSIONS_DIR  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def pytest_addoption(parser):
    parser.addoption("--skip-slow", action="store_true", help="ignore les tests lents (Whisper, interface)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--skip-slow"):
        skip = pytest.mark.skip(reason="--skip-slow")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def clean_state():
    """Chaque test part d'un dossier de données vide et d'aucune réunion en mémoire."""
    for path in (CONFIG_FILE, ENV_FILE):
        path.unlink(missing_ok=True)
    shutil.rmtree(SESSIONS_DIR, ignore_errors=True)
    discord_sessions._sessions.clear()
    yield
    discord_sessions._sessions.clear()


@pytest.fixture
def write_config():
    """Écrit un config.json (bots et profils) et renvoie son contenu."""
    def write(bots=(), profiles=()):
        data = {"bots": list(bots), "profiles": [{**config._blank_profile(p.get("name", "Profil"), ""), **p}
                                                  for p in profiles]}
        config._write(data)
        return data
    return write


@pytest.fixture
def no_background_finish(monkeypatch):
    """Empêche la rédaction automatique du résumé en fin de réunion (thread en arrière-plan)."""
    started = []
    monkeypatch.setattr(discord_sessions, "_start_finish", lambda session: started.append(session.id))
    return started
