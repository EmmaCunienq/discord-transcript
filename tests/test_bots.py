"""Pilotage des processus de bots (sans vrai bot Discord : un petit serveur HTTP local le remplace)."""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import bots


class FakeBot(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/guilds":
            self._send(200, [{"id": "g1", "name": "Studio"}])
        else:
            self._send(409, {"detail": "Un enregistrement est déjà en cours sur ce serveur."})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self._send(200, {"echo": body})

    def _send(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_bot(write_config):
    """Un « bot » qui répond sur un port local, enregistré comme en cours d'exécution."""
    httpd = HTTPServer(("127.0.0.1", 0), FakeBot)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    write_config(bots=[{"id": "b1", "name": "Principal", "token": "t"}])
    bots._running["b1"] = bots._Process(httpd.server_address[1], proc)
    yield proc
    bots._running.clear()
    proc.kill()
    httpd.shutdown()


def test_appels_au_bot(fake_bot):
    assert bots.call("b1", "/guilds") == [{"id": "g1", "name": "Studio"}]
    assert bots.call("b1", "/record", {"channel_id": "c1"}) == {"echo": {"channel_id": "c1"}}


def test_erreur_http_du_bot(fake_bot):
    with pytest.raises(bots.BotError) as err:
        bots.call("b1", "/channels")
    assert err.value.status == 409 and err.value.detail.startswith("Un enregistrement")


def test_bot_arrete(fake_bot):
    fake_bot.kill()
    fake_bot.wait()
    with pytest.raises(bots.BotError) as err:
        bots.call("b1", "/guilds")
    assert err.value.status == 503 and "token invalide" in err.value.detail


def test_bot_non_lance_ou_inconnu(write_config):
    write_config(bots=[{"id": "b1", "name": "Principal", "token": "t"}])
    with pytest.raises(bots.BotError, match="redémarre"):
        bots.call("b1", "/guilds")
    with pytest.raises(bots.BotError, match="Aucun bot choisi"):
        bots.call("", "/guilds")


def test_statuts(fake_bot, write_config):
    assert bots.statuses() == [{"id": "b1", "name": "Principal", "guilds": [{"id": "g1", "name": "Studio"}], "error": ""}]


def test_sans_bot_aucun_processus_lance(monkeypatch):
    launched = []
    monkeypatch.setattr(bots.subprocess, "Popen", lambda *a, **k: launched.append(a))
    bots.start_all("http://127.0.0.1:1")
    assert launched == [] and bots._running == {}


def test_un_processus_par_bot_avec_son_token_et_son_port(monkeypatch, write_config):
    write_config(bots=[{"id": "b1", "name": "A", "token": "t1"}, {"id": "b2", "name": "B", "token": "t2"}])
    launched = []

    class FakePopen:
        def __init__(self, cmd, cwd, env, **kwargs):
            launched.append(env)

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(bots.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(bots, "node_executable", lambda: "node")
    bots.start_all("http://127.0.0.1:8765")
    try:
        assert [(e["BOT_ID"], e["DISCORD_TOKEN"], e["BOT_NAME"]) for e in launched] == [("b1", "t1", "A"), ("b2", "t2", "B")]
        assert {e["TRANSCRIPT_SERVER"] for e in launched} == {"http://127.0.0.1:8765"}
        assert launched[0]["BOT_CONTROL_PORT"] != launched[1]["BOT_CONTROL_PORT"]
    finally:
        bots.stop_all()
    assert bots._running == {}
