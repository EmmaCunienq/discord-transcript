"""Réglages de l'application de bureau (fichier .env et bots), sans ouvrir de fenêtre."""

import sys
import os
import subprocess
from types import SimpleNamespace

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


def test_palette_transmise_au_cadre_sans_redemarrage(api, monkeypatch):
    from desktop import window_theme

    calls = []
    monkeypatch.setattr(window_theme, "apply_window_theme", lambda window, palette: calls.append((window, palette)) or True)
    api._window = object()
    palette = {"background": "#263b31", "text": "#f2e7c8", "border": "#4e624a", "dark": True}
    assert api.set_window_theme(palette) is True
    assert calls == [(api._window, palette)]


def test_actions_du_menu_integre(api, monkeypatch):
    opened = []
    closed = []
    monkeypatch.setattr(desktop.os, "startfile", opened.append)
    api._window = SimpleNamespace(destroy=lambda: closed.append(True))
    api.open_data_folder()
    api.open_log_file()
    assert opened == [desktop.DATA_DIR / "output" / "sessions", desktop.LOG_FILE]
    api.quit_app()
    assert closed == [True]


def test_lancement_sans_barre_de_menu_native(monkeypatch):
    created, started = [], []
    window = SimpleNamespace()
    identity = []
    def create_window(*args, **kwargs):
        assert identity == [desktop.APP_ID]  # l'identité doit précéder la première fenêtre
        created.append(kwargs)
        return window
    fake_webview = SimpleNamespace(settings={},
                                   create_window=create_window,
                                   start=lambda *args, **kwargs: started.append(kwargs))
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr(desktop, "setup_logging", lambda: None)
    monkeypatch.setattr(desktop, "set_app_identity", lambda: identity.append(desktop.APP_ID))
    monkeypatch.setattr(desktop, "first_launch_settings", lambda: None)
    monkeypatch.setattr(desktop, "wait_for_free_port", lambda *args: True)
    monkeypatch.setattr(desktop, "start_server", lambda: (SimpleNamespace(should_exit=False), SimpleNamespace(join=lambda **kwargs: None)))
    desktop.main()
    assert len(created) == len(started) == 1
    assert "menu" not in created[0]
    assert "menu" not in started[0]
    assert started[0]["icon"].endswith("icon.ico")


@pytest.mark.skipif(os.name != "nt", reason="Identité de la barre des tâches Windows")
def test_identite_windows_dans_un_processus_isole():
    # Ne modifie ni le processus pytest, ni les autres applications Python de l'utilisateur.
    script = """
import ctypes
from desktop.main import APP_ID, set_app_identity
assert set_app_identity()
get_id = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID
get_id.argtypes = [ctypes.POINTER(ctypes.c_wchar_p)]
get_id.restype = ctypes.c_long
value = ctypes.c_wchar_p()
assert get_id(ctypes.byref(value)) == 0
try:
    assert value.value == APP_ID
finally:
    free = ctypes.windll.ole32.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    free(ctypes.cast(value, ctypes.c_void_p))
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_enregistrement_avec_changements(api):
    result = api.save_settings(settings(OLLAMA_MODEL=" mistral-nemo ", WHISPER_LANGUAGE="auto"),
                               [{"id": "", "name": "Principal", "token": "tok"}])
    assert result == {"restart": True}
    assert desktop.read_env() == {"OLLAMA_MODEL": "mistral-nemo", "WHISPER_LANGUAGE": ""}
    assert [b["name"] for b in config.bots()] == ["Principal"]


def test_un_retour_a_la_ligne_colle_n_ajoute_pas_de_reglage(api):
    """Un \r ou un \n dans un champ ne doit pas créer une autre ligne (donc une autre clé) dans le .env."""
    api.save_settings(settings(NOTION_TOKEN="ntn\rTRANSCRIPT_ACCESS_KEY=x\n", WHISPER_MODEL="C:\\Modèles\\mon small"), [])
    assert desktop.read_env() == {"NOTION_TOKEN": "ntnTRANSCRIPT_ACCESS_KEY=x", "WHISPER_MODEL": "C:\\Modèles\\mon small"}


def test_nouveau_bot_seul_redemarre(api):
    api.save_settings(settings(), [])
    assert api.save_settings(settings(), [{"name": "Second", "token": "tok2"}]) == {"restart": True}


def test_anciens_reglages_retires_du_env(api):
    ENV_FILE.write_text("DISCORD_TOKEN=t\nDISCORD_GUILD_ID=g\nNOTION_PAGE_ID=p\nOLLAMA_MODEL=m\n", encoding="utf-8")
    assert api.save_settings(settings(OLLAMA_MODEL="m"), []) == {"restart": False}
    assert desktop.read_env() == {"OLLAMA_MODEL": "m"}


def test_premier_lancement_de_l_application_installee(monkeypatch):
    monkeypatch.setattr(desktop, "FROZEN", True)
    desktop.first_launch_settings()
    assert desktop.read_env() == {"OLLAMA_MODEL": "mistral-nemo"}  # résumé local et gratuit par défaut
    ENV_FILE.write_text("OLLAMA_MODEL=autre\n", encoding="utf-8")
    desktop.first_launch_settings()  # réglages existants : rien n'est remplacé
    assert desktop.read_env() == {"OLLAMA_MODEL": "autre"}


def test_premier_lancement_en_developpement_reprend_le_env_du_projet(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("OLLAMA_MODEL=llama3\nNOTION_TOKEN=ntn\n", encoding="utf-8")
    monkeypatch.setattr(desktop, "FROZEN", False)
    monkeypatch.setattr(desktop, "PROJECT_DIR", tmp_path)
    desktop.first_launch_settings()
    assert desktop.read_env() == {"OLLAMA_MODEL": "llama3", "NOTION_TOKEN": "ntn"}
