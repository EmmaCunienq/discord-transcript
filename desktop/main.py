"""Application de bureau Discord Transcript.

Ouvre l'interface dans une fenêtre native (WebView2) et fait tourner, en arrière-plan,
le serveur de transcription et le bot Discord. Fermer la fenêtre arrête tout.

    python desktop/main.py          (développement)
    DiscordTranscript.exe               (version emballée par PyInstaller)
"""

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_NAME = "Discord Transcript"
PORT = 8765          # fixe : la mémoire de la page (localStorage) dépend du port
FROZEN = getattr(sys, "frozen", False)
PROJECT_DIR = Path(__file__).resolve().parent.parent

# Environnement d'origine, avant lecture du .env : c'est lui qu'on transmet au redémarrage,
# sinon les anciennes valeurs masqueraient celles qu'on vient d'enregistrer.
ORIGINAL_ENV = dict(os.environ)

DATA_DIR = Path(os.getenv("TRANSCRIPT_DATA_DIR") or Path(os.getenv("LOCALAPPDATA", Path.home())) / "DiscordTranscript")
DATA_DIR.mkdir(parents=True, exist_ok=True)
ENV_FILE = DATA_DIR / ".env"
LOG_FILE = DATA_DIR / "logs" / "app.log"

SETTINGS_KEYS = ["OLLAMA_MODEL", "ANTHROPIC_API_KEY", "WHISPER_MODEL", "WHISPER_LANGUAGE", "NOTION_TOKEN"]
# Réglages d'avant les profils, repris dans config.json (bots, profils) puis retirés du .env
LEGACY_KEYS = ["DISCORD_TOKEN", "DISCORD_GUILD_ID", "NOTION_PAGE_ID"]

if not FROZEN:
    sys.path.insert(0, str(PROJECT_DIR))


def setup_logging() -> None:
    """Sans console (version emballée), toute la sortie va dans logs/app.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5_000_000:
        LOG_FILE.replace(LOG_FILE.with_suffix(".old.log"))
    if FROZEN or sys.stdout is None:
        log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    print(f"\n=== {APP_NAME} — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")


def first_launch_import() -> None:
    """En développement, reprend le .env du projet s'il n'y a pas encore de réglages."""
    project_env = PROJECT_DIR / ".env"
    if not ENV_FILE.exists() and not FROZEN and project_env.exists():
        ENV_FILE.write_bytes(project_env.read_bytes())


# --- Réglages (.env) ------------------------------------------------------------
def read_env() -> dict[str, str]:
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                values[key.strip()] = value.strip().strip("\"'")
    return values


def write_env(updates: dict[str, str]) -> None:
    """Met à jour les clés données en gardant le reste du fichier (commentaires, autres clés)."""
    lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines() if ENV_FILE.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        key = line.strip().partition("=")[0].strip()
        if key in remaining:
            value = remaining.pop(key)
            if value is not None:
                out.append(f"{key}={value}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in remaining.items() if v is not None]
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


class Api:
    """Fonctions appelables depuis la page de réglages (window.pywebview.api)."""

    def __init__(self) -> None:
        # Préfixe « _ » : pywebview n'expose pas (et ne parcourt pas) cet attribut côté page
        self._window = None

    def get_settings(self) -> dict:
        from app import config

        env = read_env()
        values = {k: env.get(k, "") for k in SETTINGS_KEYS}
        if "WHISPER_LANGUAGE" in env and env["WHISPER_LANGUAGE"] == "":
            values["WHISPER_LANGUAGE"] = "auto"
        return {"values": values, "bots": config.bots()}

    def save_settings(self, values: dict, bots: list) -> dict:
        """Enregistre les réglages et les bots ; indique si un redémarrage est nécessaire."""
        from app import config

        updates: dict[str, str | None] = dict.fromkeys(LEGACY_KEYS)
        for k in SETTINGS_KEYS:
            v = str(values.get(k, "")).strip().replace("\n", "")
            if k == "WHISPER_LANGUAGE" and v.lower() == "auto":
                updates[k] = ""      # présent mais vide = détection automatique
            else:
                updates[k] = v or None  # vide = on retire la ligne (valeur par défaut)
        current = read_env()
        env_changed = any(current.get(k) != v for k, v in updates.items() if k not in LEGACY_KEYS)
        write_env(updates)
        bots_changed = config.set_bots(bots)
        return {"restart": env_changed or bots_changed}

    def open_data_folder(self) -> None:
        folder = DATA_DIR / "output" / "sessions"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def restart(self) -> None:
        cmd = [sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]
        # PYINSTALLER_RESET_ENVIRONMENT : le nouvel .exe repart de zéro (pas de dossier partagé)
        env = {**ORIGINAL_ENV, "TRANSCRIPT_RESTART_WAIT": "1", "PYINSTALLER_RESET_ENVIRONMENT": "1"}
        subprocess.Popen(cmd, env=env, close_fds=True)
        if self._window:
            self._window.destroy()


# --- Serveur ----------------------------------------------------------------------
def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_free_port(port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while port_in_use(port):
        if time.time() > deadline:
            return False
        time.sleep(0.3)
    return True


def start_server():
    os.environ["TRANSCRIPT_DATA_DIR"] = str(DATA_DIR)
    os.environ["TRANSCRIPT_SERVER"] = f"http://127.0.0.1:{PORT}"
    os.environ.pop("TRANSCRIPT_ACCESS_KEY", None)  # app locale : pas de clé d'accès

    import uvicorn

    from app.server import app

    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="info", use_colors=False,
                            access_log=False)  # la page interroge le serveur chaque seconde
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    return server, thread


def wait_until_ready(window, server, thread) -> None:
    url = f"http://127.0.0.1:{PORT}/"
    while thread.is_alive():
        if server.started:
            try:
                urllib.request.urlopen(url, timeout=2).close()
                window.load_url(url)
                return
            except OSError:
                pass
        time.sleep(0.4)
    window.load_html(message_page("Le serveur n'a pas pu démarrer.",
                                  f"Détails dans le journal : {LOG_FILE}"))


def message_page(title: str, detail: str = "", spinner: bool = False) -> str:
    spin = '<div class="spin"></div>' if spinner else ""
    return f"""<!doctype html><html lang="fr"><meta charset="utf-8"><style>
    body{{margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
    gap:14px;background:#b0bc99;color:#2f4538;font:15px "Segoe UI",sans-serif;text-align:center;padding:24px}}
    @media (prefers-color-scheme:dark){{body{{background:#233a2f;color:#f2e7c8}}}}
    h1{{font-size:20px;margin:0}} p{{margin:0;opacity:.75;max-width:520px}}
    .spin{{width:34px;height:34px;border:3px solid currentColor;border-right-color:transparent;border-radius:50%;
    animation:r 1s linear infinite}} @keyframes r{{to{{transform:rotate(360deg)}}}}
    </style><body>{spin}<h1>{title}</h1><p>{detail}</p></body></html>"""


# --- Fenêtre ----------------------------------------------------------------------
def show_error_box(text: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10)
    else:
        print(text)


def main() -> None:
    setup_logging()
    first_launch_import()

    # Après « Enregistrer et redémarrer », l'ancienne instance libère les ports en quittant.
    wait = 20 if os.getenv("TRANSCRIPT_RESTART_WAIT") else 0
    if not wait_for_free_port(PORT, wait):
        show_error_box(f"{APP_NAME} est déjà ouvert (ou le port {PORT} est utilisé par un autre programme).")
        return

    import webview
    from webview.menu import Menu, MenuAction, MenuSeparator

    webview.settings["ALLOW_DOWNLOADS"] = True       # bouton « Télécharger .md »
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    api = Api()
    server, thread = start_server()
    base = f"http://127.0.0.1:{PORT}"
    window = webview.create_window(
        APP_NAME,
        html=message_page("Démarrage…", "Chargement du modèle de transcription. Au premier lancement, "
                          "il est téléchargé (~500 Mo) : cela peut prendre quelques minutes.", spinner=True),
        js_api=api, width=1100, height=860, min_size=(420, 520), text_select=True,
    )
    api._window = window

    menu = [
        Menu("Discord Transcript", [
            MenuAction("Accueil", lambda: window.load_url(base + "/")),
            MenuAction("Réglages…", lambda: window.load_url(base + "/static/settings.html")),
            MenuSeparator(),
            MenuAction("Ouvrir le dossier des réunions", api.open_data_folder),
            MenuAction("Ouvrir le journal", lambda: os.startfile(LOG_FILE)),
            MenuSeparator(),
            MenuAction("Quitter", window.destroy),
        ]),
    ]

    webview.start(
        wait_until_ready, (window, server, thread),
        menu=menu, private_mode=False, storage_path=str(DATA_DIR / "webview"),
    )

    # Fenêtre fermée : arrêt propre du serveur (qui arrête aussi le bot Discord)
    server.should_exit = True
    thread.join(timeout=15)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        show_error_box(f"{APP_NAME} s'est arrêté sur une erreur.\nDétails : {LOG_FILE}")
        raise
