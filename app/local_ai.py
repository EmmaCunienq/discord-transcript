"""Ollama géré par l'application : trouvé, téléchargé si besoin, lancé et arrêté avec elle.

Au démarrage (si OLLAMA_MODEL est défini), en arrière-plan :
1. Ollama répond déjà (application Ollama ouverte) : on l'utilise tel quel.
2. Sinon, Ollama est installé (ou déjà téléchargé par l'app) : on lance `ollama serve`.
3. Sinon, on télécharge la version Windows officielle dans <données>/ollama, puis on la lance.
4. Le modèle manquant est téléchargé (`/api/pull`).
Seul un Ollama lancé par l'app est arrêté à sa fermeture.
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import envfile
from .paths import DATA_DIR

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
INSTALL_DIR = DATA_DIR / "ollama"
_ARCH = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
DOWNLOAD_URL = f"https://github.com/ollama/ollama/releases/latest/download/ollama-windows-{_ARCH}.zip"
# Ollama (archive puis dossier extrait) et un modèle d'environ 7 Go
REQUIRED_FREE_BYTES = 12 * 1024**3
STARTUP_TIMEOUT_S = 60

# phase : off | checking | downloading | extracting | starting | pulling | ready | error
_state = {"phase": "off", "progress": None, "detail": "", "error": "", "model": ""}
_ready = threading.Event()
_proc: subprocess.Popen | None = None


def status() -> dict:
    return dict(_state)


def _set(phase: str, detail: str = "", progress: float | None = None, error: str = "") -> None:
    _state.update(phase=phase, detail=detail, progress=progress, error=error)


def wait_ready(timeout: float | None = None) -> bool:
    """Attend qu'Ollama et le modèle soient prêts (faux si échec ou délai dépassé)."""
    deadline = None if timeout is None else time.time() + timeout
    while not _ready.is_set():
        if _state["phase"] in ("error", "off") or (deadline and time.time() > deadline):
            return False
        _ready.wait(1)
    return True


# --- Ollama ----------------------------------------------------------------------------
def _api(path: str, body: dict | None = None, timeout: float = 5):
    req = urllib.request.Request(OLLAMA_URL + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _responding() -> bool:
    # Délai court : sous Windows, une connexion refusée met sinon ~2 s à échouer
    try:
        with _api("/api/version", timeout=0.5):
            return True
    except (OSError, ValueError):
        return False


def find_executable() -> str | None:
    """Ollama déjà téléchargé par l'app, sinon une installation existante."""
    name = "ollama.exe" if os.name == "nt" else "ollama"
    candidates = sorted(INSTALL_DIR.rglob(name)) if INSTALL_DIR.is_dir() else []
    if os.getenv("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama" / "ollama.exe")
    found = next((str(p) for p in candidates if p.is_file()), None)
    return found or shutil.which("ollama")


def _expected_sha256(archive_url: str) -> str:
    """Somme SHA-256 publiée avec la version d'Ollama téléchargée (fichier sha256sum.txt de la release)."""
    base, _, name = archive_url.rpartition("/")
    with urllib.request.urlopen(f"{base}/sha256sum.txt", timeout=30) as res:
        for line in res.read().decode("utf-8", "replace").splitlines():
            digest, _, file = line.strip().partition("  ")
            if file.removeprefix("./") == name:
                return digest.lower()
    raise RuntimeError(f"somme de contrôle de {name} introuvable : téléchargement annulé.")


def _download() -> str:
    free = shutil.disk_usage(DATA_DIR).free
    if free < REQUIRED_FREE_BYTES:
        raise RuntimeError(f"espace disque insuffisant : {free / 1024**3:.1f} Go libres, "
                           f"{REQUIRED_FREE_BYTES / 1024**3:.0f} Go nécessaires pour Ollama et le modèle.")
    INSTALL_DIR.parent.mkdir(parents=True, exist_ok=True)
    archive = INSTALL_DIR.with_name("ollama-download.zip")
    staging = INSTALL_DIR.with_name("ollama-extract")
    try:
        _set("downloading", "Téléchargement d'Ollama…", 0.0)
        digest = hashlib.sha256()
        with urllib.request.urlopen(DOWNLOAD_URL, timeout=60) as res, open(archive, "wb") as out:
            release_url = res.geturl()  # après redirection : l'adresse de la version précise
            total = int(res.headers.get("Content-Length") or 0)
            done = 0
            while chunk := res.read(1 << 20):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total:
                    _state["progress"] = done / total
        if digest.hexdigest() != _expected_sha256(release_url):
            raise RuntimeError("archive Ollama altérée (somme de contrôle différente) : téléchargement annulé.")
        # Extraction à côté puis renommage : une installation interrompue ne laisse pas un
        # ollama.exe incomplet, qui serait sinon relancé (et en échec) à chaque démarrage.
        _set("extracting", "Installation d'Ollama…")
        shutil.rmtree(staging, ignore_errors=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(staging)
        shutil.rmtree(INSTALL_DIR, ignore_errors=True)
        staging.rename(INSTALL_DIR)
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
    exe = find_executable()
    if not exe:
        raise RuntimeError("archive Ollama inattendue : ollama.exe introuvable.")
    return exe


def _start(exe: str) -> None:
    global _proc
    _set("starting", "Démarrage d'Ollama…")
    host = OLLAMA_URL.split("://", 1)[-1]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    _proc = subprocess.Popen([exe, "serve"], env=envfile.child_env(OLLAMA_HOST=host),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    deadline = time.time() + STARTUP_TIMEOUT_S
    while not _responding():
        if _proc.poll() is not None:
            raise RuntimeError("Ollama s'est arrêté au démarrage.")
        if time.time() > deadline:
            raise RuntimeError("Ollama ne répond pas.")
        time.sleep(0.5)


def _has_model(model: str) -> bool:
    with _api("/api/tags") as res:
        names = {m["name"] for m in json.load(res).get("models", [])}
    wanted = model if ":" in model else f"{model}:latest"
    return wanted in names or model in names


def _pull(model: str) -> None:
    _set("pulling", f"Téléchargement du modèle {model}…", 0.0)
    with _api("/api/pull", {"model": model, "stream": True}, timeout=600) as res:
        for line in res:
            msg = json.loads(line)
            if msg.get("error"):
                raise RuntimeError(f"téléchargement du modèle impossible : {msg['error']}")
            if msg.get("total"):
                _state["progress"] = msg.get("completed", 0) / msg["total"]


def prepare(model: str) -> None:
    """Rend Ollama et le modèle disponibles (appelé dans un thread au démarrage du serveur)."""
    _ready.clear()
    _state["model"] = model
    try:
        _set("checking", "Recherche d'Ollama…")
        if not _responding():
            _start(find_executable() or _download())
        if not _has_model(model):
            _pull(model)
        _set("ready", f"Résumé local prêt ({model})")
        _ready.set()
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as e:
        _set("error", error=str(e))
        print(f"[ollama] {e}")


def start_background(model: str) -> None:
    if model:
        threading.Thread(target=prepare, args=(model,), daemon=True, name="ollama").start()


def stop() -> None:
    """Arrête Ollama s'il a été lancé par l'app (pas l'application Ollama de l'utilisateur)."""
    global _proc
    if _proc and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _proc.kill()
    _proc = None
