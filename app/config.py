"""Bots Discord et profils d'enregistrement, enregistrés dans <données>/config.json.

- Un bot : un nom et un token ; chacun tourne dans son propre processus Node.
- Un profil (un onglet de la page) : le bot à utiliser, le salon vocal, la page Notion
  où créer les comptes rendus et un contexte par défaut pour le résumé.
"""

import json
import os
import threading
import uuid

from .paths import CONFIG_FILE

# channel_name / notion_title : libellés affichés dans l'onglet, mémorisés au moment du choix
PROFILE_FIELDS = ("name", "bot_id", "guild_id", "channel_id", "channel_name", "notion_page", "notion_title", "context")

_lock = threading.Lock()


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _blank_profile(name: str, bot_id: str) -> dict:
    return {"id": _new_id(), "name": name, "bot_id": bot_id, **dict.fromkeys(PROFILE_FIELDS[2:], "")}


def _read() -> dict:
    if not CONFIG_FILE.exists():
        return {"bots": [], "profiles": []}
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def _write(data: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def _migrate() -> None:
    """Premier lancement après l'arrivée des profils : reprend le bot et les réglages d'avant
    (DISCORD_TOKEN, DISCORD_GUILD_ID, NOTION_PAGE_ID) dans un premier bot et un premier profil."""
    if CONFIG_FILE.exists() or not os.getenv("DISCORD_TOKEN"):
        return
    bot = {"id": _new_id(), "name": "Bot principal", "token": os.environ["DISCORD_TOKEN"]}
    profile = {**_blank_profile("Réunions", bot["id"]), "guild_id": os.getenv("DISCORD_GUILD_ID", ""),
               "notion_page": os.getenv("NOTION_PAGE_ID", "")}
    _write({"bots": [bot], "profiles": [profile]})


# --- Bots ----------------------------------------------------------------------------
def bots() -> list[dict]:
    with _lock:
        return _read()["bots"]


def set_bots(items: list[dict]) -> bool:
    """Remplace la liste des bots (réglages) ; renvoie vrai si elle a changé."""
    clean = [{"id": b.get("id") or _new_id(), "name": b["name"].strip() or "Bot", "token": b["token"].strip()}
             for b in items if b.get("token", "").strip()]
    with _lock:
        data = _read()
        if clean == data["bots"]:
            return False
        data["bots"] = clean
        _write(data)
        return True


# --- Profils ---------------------------------------------------------------------------
def profiles() -> list[dict]:
    with _lock:
        return _read()["profiles"]


def profile(profile_id: str) -> dict | None:
    return next((p for p in profiles() if p["id"] == profile_id), None)


def create_profile(name: str) -> dict:
    with _lock:
        data = _read()
        bot_id = data["bots"][0]["id"] if data["bots"] else ""
        new = _blank_profile(name, bot_id)
        data["profiles"].append(new)
        _write(data)
        return new


def update_profile(profile_id: str, changes: dict) -> dict | None:
    with _lock:
        data = _read()
        target = next((p for p in data["profiles"] if p["id"] == profile_id), None)
        if target:
            target.update({k: str(v) for k, v in changes.items() if k in PROFILE_FIELDS and v is not None})
            _write(data)
        return target


def delete_profile(profile_id: str) -> None:
    with _lock:
        data = _read()
        data["profiles"] = [p for p in data["profiles"] if p["id"] != profile_id]
        _write(data)


def match_profile(bot_id: str, guild_id: str, channel_id: str) -> str:
    """Profil d'un enregistrement lancé depuis Discord (/enregistrer) : celui du même bot et du
    même salon, sinon du même serveur, sinon du même bot."""
    mine = [p for p in profiles() if p["bot_id"] == bot_id]
    for test in (lambda p: p["channel_id"] == channel_id, lambda p: p["guild_id"] == guild_id, lambda p: True):
        found = next((p for p in mine if test(p)), None)
        if found:
            return found["id"]
    return ""


_migrate()
