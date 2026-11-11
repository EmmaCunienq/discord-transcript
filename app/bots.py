"""Processus des bots Discord : un processus Node par bot, piloté en HTTP local.

Chaque bot écoute sur son propre port (choisi libre au démarrage) ; le serveur lui
demande ses salons, de rejoindre un salon ou d'arrêter un enregistrement.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import config, envfile
from .paths import BOT_DIR, node_executable


class BotError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class _Process:
    port: int
    proc: subprocess.Popen
    key: str  # secret exigé par l'API de contrôle du bot (en-tête X-Control-Key)


_running: dict[str, _Process] = {}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_all(server_url: str) -> None:
    node = node_executable()
    configured = config.bots()
    if not configured:
        return
    if not (BOT_DIR / "node_modules").exists() or not node:
        print("[discord] bot non installé : lance `npm install` dans bot/")
        return
    # Dans l'app de bureau, pas de console : la sortie des bots va dans le journal et
    # aucune fenêtre noire ne s'ouvre sous Windows.
    log = sys.stdout if sys.stdout and hasattr(sys.stdout, "fileno") else None
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for bot in configured:
        port, key = _free_port(), secrets.token_urlsafe(24)
        # Chaque bot ne reçoit que son propre token (pas ceux des autres, ni les clés Notion / Anthropic)
        env = envfile.child_env(
            DISCORD_TOKEN=bot["token"], BOT_ID=bot["id"], BOT_NAME=bot["name"], BOT_CONTROL_PORT=str(port),
            BOT_CONTROL_KEY=key, TRANSCRIPT_SERVER=server_url,
            TRANSCRIPT_ACCESS_KEY=os.getenv("TRANSCRIPT_ACCESS_KEY", ""),
        )
        proc = subprocess.Popen([node, "index.js"], cwd=BOT_DIR, env=env, stdout=log,
                                stderr=subprocess.STDOUT if log else None, creationflags=flags)
        _running[bot["id"]] = _Process(port, proc, key)


def stop_all() -> None:
    for bot in _running.values():
        if bot.proc.poll() is None:
            bot.proc.terminate()
    for bot in _running.values():
        try:
            bot.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            bot.proc.kill()
    _running.clear()


def call(bot_id: str, path: str, payload: dict | None = None):
    """Requête au bot ; BotError avec un message lisible si elle échoue."""
    bot = _running.get(bot_id)
    if bot is None:
        known = any(b["id"] == bot_id for b in config.bots())
        raise BotError(503, "Bot non lancé (redémarre l'application)." if known
                       else "Aucun bot choisi pour ce profil (ajoute un bot dans les Réglages).")
    if bot.proc.poll() is not None:
        raise BotError(503, "Le bot s'est arrêté (token invalide ? voir le journal).")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{bot.port}{path}", data=data, method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json", "X-Control-Key": bot.key},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get("detail", str(e))
        except ValueError:
            detail = str(e)
        raise BotError(e.code, detail) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise BotError(503, "Le bot démarre, réessaie dans un instant.") from e


def statuses() -> list[dict]:
    """Bots configurés, avec leurs serveurs Discord s'ils sont connectés."""
    out = []
    for bot in config.bots():
        item = {"id": bot["id"], "name": bot["name"], "guilds": [], "error": ""}
        try:
            item["guilds"] = call(bot["id"], "/guilds")
        except BotError as e:
            item["error"] = e.detail
        out.append(item)
    return out
