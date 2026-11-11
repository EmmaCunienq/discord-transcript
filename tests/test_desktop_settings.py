"""Réglages de l'application de bureau (fichier .env et bots), sans ouvrir de fenêtre."""

import sys

import pytest

from app import config
from app.paths import ENV_FILE
from tests.conftest import ROOT

sys.path.insert(0, str(ROOT / "desktop"))
import main as desktop  # noqa: E402


@pytest.fixture
def api():
    return desktop.Api()


def settings(**values):
    return {k: values.get(k, "") for k in desktop.SETTINGS_KEYS}


def test_le_lanceur_utilise_le_dossier_de_donnees_des_tests():
    assert desktop.ENV_FILE == ENV_FILE


def test_ecriture_du_env_garde_commentaires_et_autres_cles():
    ENV_FILE.write_text("# commentaire\nAUTRE=1\nOLLAMA_MODEL=ancien\n", encoding="utf-8")
    desktop.write_env({"OLLAMA_MODEL": "mistral-nemo", "NOTION_TOKEN": "ntn", "WHISPER_MODEL": None})
    assert ENV_FILE.read_text(encoding="utf-8") == "# commentaire\nAUTRE=1\nOLLAMA_MODEL=mistral-nemo\nNOTION_TOKEN=ntn\n"
    assert desktop.read_env() == {"AUTRE": "1", "OLLAMA_MODEL": "mistral-nemo", "NOTION_TOKEN": "ntn"}


def test_lecture_des_reglages(api, write_config):
    ENV_FILE.write_text("OLLAMA_MODEL=mistral-nemo\nWHISPER_LANGUAGE=\n", encoding="utf-8")
    write_config(bots=[{"id": "b1", "name": "Principal", "token": "t"}])
    got = api.get_settings()
    assert got["values"]["OLLAMA_MODEL"] == "mistral-nemo"
    assert got["values"]["WHISPER_LANGUAGE"] == "auto"  # présent mais vide = détection automatique
    assert got["bots"] == [{"id": "b1", "name": "Principal", "token": "t"}]


def test_enregistrement_sans_changement_ne_redemarre_pas(api):
    assert api.save_settings(settings(), []) == {"restart": False}


def test_enregistrement_avec_changements(api):
    result = api.save_settings(settings(OLLAMA_MODEL=" mistral-nemo ", WHISPER_LANGUAGE="auto"),
                               [{"id": "", "name": "Principal", "token": "tok"}])
    assert result == {"restart": True}
    assert desktop.read_env() == {"OLLAMA_MODEL": "mistral-nemo", "WHISPER_LANGUAGE": ""}
    assert [b["name"] for b in config.bots()] == ["Principal"]


def test_nouveau_bot_seul_redemarre(api):
    api.save_settings(settings(), [])
    assert api.save_settings(settings(), [{"name": "Second", "token": "tok2"}]) == {"restart": True}


def test_anciens_reglages_retires_du_env(api):
    ENV_FILE.write_text("DISCORD_TOKEN=t\nDISCORD_GUILD_ID=g\nNOTION_PAGE_ID=p\nOLLAMA_MODEL=m\n", encoding="utf-8")
    assert api.save_settings(settings(OLLAMA_MODEL="m"), []) == {"restart": False}
    assert desktop.read_env() == {"OLLAMA_MODEL": "m"}
