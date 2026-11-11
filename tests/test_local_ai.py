"""Ollama géré par l'app : un faux serveur Ollama et un faux téléchargement remplacent les vrais
(rien n'est téléchargé, l'installation Ollama de l'ordinateur n'est pas utilisée)."""

import hashlib
import io
import json
import socket
import threading
import time
import zipfile
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import local_ai, summarizer


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeOllama:
    """Serveur Ollama simulé : /api/version, /api/tags, /api/pull (en flux)."""

    def __init__(self, port, models=("mistral-nemo:latest",), pull_error=""):
        self.models, self.pulled = set(models), []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/api/version":
                    self._json({"version": "0.40.16"})
                elif self.path == "/api/tags":
                    self._json({"models": [{"name": m} for m in sorted(fake.models)]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.pulled.append(body["model"])
                self.send_response(200)
                self.end_headers()
                if pull_error:
                    self.wfile.write((json.dumps({"error": pull_error}) + "\n").encode())
                    return
                for done in (0, 50, 100):
                    self.wfile.write((json.dumps({"status": "pulling", "total": 100, "completed": done}) + "\n").encode())
                fake.models.add(body["model"] + ("" if ":" in body["model"] else ":latest"))
                self.wfile.write(b'{"status":"success"}\n')

            def _json(self, data):
                raw = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Ollama introuvable sur la machine, dossier d'installation temporaire, état remis à zéro."""
    port = free_port()
    monkeypatch.setattr(local_ai, "OLLAMA_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(local_ai, "INSTALL_DIR", tmp_path / "ollama")
    monkeypatch.setattr(local_ai, "STARTUP_TIMEOUT_S", 5)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(local_ai.shutil, "which", lambda name: None)
    local_ai._state.update(phase="off", progress=None, detail="", error="", model="")
    local_ai._ready.clear()
    local_ai._proc = None
    servers = []
    yield namedtuple("Env", "port tmp servers")(port, tmp_path, servers)
    for s in servers:
        s.close()
    local_ai._state.update(phase="off", progress=None, detail="", error="", model="")
    local_ai._ready.clear()
    local_ai._proc = None


@pytest.fixture
def popen(monkeypatch, env):
    """`ollama serve` simulé : démarre le faux serveur Ollama sur le port attendu."""
    launched = []

    class FakeProcess:
        def __init__(self, cmd, env, **kwargs):
            launched.append((cmd, env["OLLAMA_HOST"]))
            self.code = None
            self.server = FakeOllama(int(env["OLLAMA_HOST"].rsplit(":", 1)[1]))
            launched_servers.append(self.server)

        def poll(self):
            return self.code

        def terminate(self):
            self.code = 0
            self.server.close()

        def wait(self, timeout=None):
            return 0

    launched_servers = env.servers
    monkeypatch.setattr(local_ai.subprocess, "Popen", FakeProcess)
    return launched


def test_ollama_deja_ouvert_utilise_tel_quel(env, popen):
    env.servers.append(FakeOllama(env.port))
    local_ai.prepare("mistral-nemo")
    assert local_ai.status()["phase"] == "ready" and local_ai.wait_ready(0)
    assert popen == []                       # rien lancé
    local_ai.stop()                          # et rien arrêté : ce n'est pas le nôtre
    assert local_ai._responding()


def test_ollama_installe_lance_puis_arrete_avec_l_app(env, popen):
    exe = env.tmp / "localappdata" / "Programs" / "Ollama" / "ollama.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    local_ai.prepare("mistral-nemo")
    assert local_ai.status()["phase"] == "ready"
    assert popen == [([str(exe), "serve"], f"127.0.0.1:{env.port}")]
    local_ai.stop()
    assert not local_ai._responding()


def zip_payload():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("ollama.exe", b"exe")
        z.writestr("lib/ollama/ggml.dll", b"dll")
    return archive.getvalue()


@pytest.fixture
def release(monkeypatch):
    """Fausse release GitHub : « latest » redirige vers une version précise, qui publie l'archive
    et son fichier sha256sum.txt. `serve(archive, sums)` choisit ce qui est servi."""
    served = {}

    class Release(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/latest/ollama-windows-amd64.zip":
                self.send_response(302)
                self.send_header("Location", "/v9.9.9/ollama-windows-amd64.zip")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = served.get(self.path)
            self.send_response(200 if body is not None else 404)
            body = body if body is not None else b""
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Release)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(local_ai, "DOWNLOAD_URL", f"http://127.0.0.1:{httpd.server_address[1]}/latest/ollama-windows-amd64.zip")
    monkeypatch.setattr(local_ai.shutil, "disk_usage", lambda path: namedtuple("U", "total used free")(0, 0, 50 * 1024**3))

    def serve(archive, sums=None):
        if sums is None:
            sums = f"{'0' * 64}  ./OllamaSetup.exe\n{hashlib.sha256(archive).hexdigest()}  ./ollama-windows-amd64.zip\n"
        served["/v9.9.9/ollama-windows-amd64.zip"] = archive
        if sums:
            served["/v9.9.9/sha256sum.txt"] = sums.encode()

    yield serve
    httpd.shutdown()


def assert_nothing_installed(env, popen):
    """Une installation ratée ne doit pas laisser un ollama.exe incomplet, relancé à chaque démarrage."""
    assert local_ai.status()["phase"] == "error" and popen == []
    assert not local_ai.INSTALL_DIR.exists()
    assert not (env.tmp / "ollama-download.zip").exists() and not (env.tmp / "ollama-extract").exists()
    assert local_ai.find_executable() is None


def test_ollama_absent_telecharge_verifie_puis_lance(env, popen, release):
    release(zip_payload())
    local_ai.prepare("mistral-nemo")
    exe = local_ai.INSTALL_DIR / "ollama.exe"
    assert local_ai.status()["phase"] == "ready"
    assert exe.read_bytes() == b"exe" and (local_ai.INSTALL_DIR / "lib/ollama/ggml.dll").exists()
    assert not (env.tmp / "ollama-download.zip").exists()  # archive supprimée après extraction
    assert popen[0][0] == [str(exe), "serve"]
    # Au lancement suivant, la copie téléchargée est retrouvée : pas de nouveau téléchargement
    assert local_ai.find_executable() == str(exe)


def test_archive_ollama_alteree_refusee(env, popen, release):
    release(zip_payload(), sums=f"{'a' * 64}  ./ollama-windows-amd64.zip\n")
    local_ai.prepare("mistral-nemo")
    assert "somme de contrôle différente" in local_ai.status()["error"]
    assert_nothing_installed(env, popen)


def test_somme_de_controle_absente_refusee(env, popen, release):
    release(zip_payload(), sums=f"{'a' * 64}  ./autre-fichier.zip\n")
    local_ai.prepare("mistral-nemo")
    assert "somme de contrôle de ollama-windows-amd64.zip introuvable" in local_ai.status()["error"]
    assert_nothing_installed(env, popen)


def test_archive_ollama_corrompue_ne_laisse_pas_d_installation_partielle(env, popen, release):
    release(b"PK\x03\x04 pas une archive")  # somme correcte, mais zip illisible
    local_ai.prepare("mistral-nemo")
    assert_nothing_installed(env, popen)


def test_ollama_lance_sans_les_cles_de_l_app(env, monkeypatch):
    exe = env.tmp / "localappdata" / "Programs" / "Ollama" / "ollama.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("NOTION_TOKEN", "ntn-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    envs = []

    class Crashing:
        def __init__(self, cmd, env, **kwargs):
            envs.append(env)

        def poll(self):
            return 1

    monkeypatch.setattr(local_ai.subprocess, "Popen", Crashing)
    local_ai.prepare("mistral-nemo")
    assert envs[0]["OLLAMA_HOST"] == f"127.0.0.1:{env.port}"
    assert "NOTION_TOKEN" not in envs[0] and "ANTHROPIC_API_KEY" not in envs[0]


def test_espace_disque_insuffisant(env, popen, monkeypatch):
    monkeypatch.setattr(local_ai.shutil, "disk_usage", lambda path: namedtuple("U", "total used free")(0, 0, 2 * 1024**3))
    local_ai.prepare("mistral-nemo")
    state = local_ai.status()
    assert state["phase"] == "error" and "espace disque insuffisant : 2.0 Go libres" in state["error"]
    assert popen == [] and not local_ai.wait_ready(0)


def test_modele_manquant_telecharge_avec_progression(env, popen):
    server = FakeOllama(env.port, models=())
    env.servers.append(server)
    seen = []
    original = local_ai._set

    def spy(phase, detail="", progress=None, error=""):
        original(phase, detail, progress, error)
        seen.append(phase)

    local_ai._set = spy
    try:
        local_ai.prepare("mistral-nemo")
    finally:
        local_ai._set = original
    assert server.pulled == ["mistral-nemo"]
    assert seen == ["checking", "pulling", "ready"]
    assert local_ai.status()["phase"] == "ready"


def test_modele_avec_etiquette_deja_present(env, popen):
    server = FakeOllama(env.port, models=("qwen2.5:7b",))
    env.servers.append(server)
    local_ai.prepare("qwen2.5:7b")
    assert server.pulled == [] and local_ai.status()["phase"] == "ready"


def test_telechargement_du_modele_en_echec(env):
    env.servers.append(FakeOllama(env.port, models=(), pull_error="file does not exist"))
    local_ai.prepare("modele-inconnu")
    assert local_ai.status()["phase"] == "error"
    assert "file does not exist" in local_ai.status()["error"]


def test_ollama_qui_s_arrete_au_demarrage(env, monkeypatch):
    exe = env.tmp / "localappdata" / "Programs" / "Ollama" / "ollama.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    class Crashing:
        def __init__(self, *a, **k):
            pass

        def poll(self):
            return 1

    monkeypatch.setattr(local_ai.subprocess, "Popen", Crashing)
    local_ai.prepare("mistral-nemo")
    assert local_ai.status()["phase"] == "error"
    assert local_ai.status()["error"] == "Ollama s'est arrêté au démarrage."


def test_sans_modele_rien_n_est_prepare(env, popen):
    local_ai.start_background("")
    time.sleep(0.1)
    assert local_ai.status()["phase"] == "off" and popen == []


# --- Résumé pendant la préparation ------------------------------------------------------
def test_resume_a_la_demande_pendant_la_preparation(env, monkeypatch):
    monkeypatch.setattr(summarizer, "OLLAMA_MODEL", "mistral-nemo")
    local_ai._set("pulling", "Téléchargement du modèle mistral-nemo…", 0.42)
    with pytest.raises(summarizer.SummaryError, match=r"se prépare — Téléchargement du modèle mistral-nemo… \(42%\)"):
        list(summarizer.generate("texte", ""))
    local_ai._set("error", error="espace disque insuffisant")
    with pytest.raises(summarizer.SummaryError, match="Ollama indisponible : espace disque insuffisant"):
        list(summarizer.generate("texte", ""))


def test_fin_de_reunion_attend_qu_ollama_soit_pret(env, monkeypatch):
    monkeypatch.setattr(summarizer, "OLLAMA_MODEL", "mistral-nemo")
    monkeypatch.setattr(summarizer, "_generate_ollama", lambda content: iter(["Résumé."]))
    local_ai._set("pulling", "…", 0.9)

    def finish_download():
        time.sleep(0.3)
        local_ai._set("ready")
        local_ai._ready.set()

    threading.Thread(target=finish_download).start()
    started = time.time()
    assert summarizer.summarize("texte") == "Résumé."
    assert time.time() - started >= 0.25
