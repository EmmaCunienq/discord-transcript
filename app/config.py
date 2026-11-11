"""Bots Discord et profils d'enregistrement, enregistrés dans <données>/config.json.

- Un bot : un nom et un token ; chacun tourne dans son propre processus Node.
- Un profil (un onglet de la page) : le bot à utiliser, le salon vocal, la page Notion
  où créer les comptes rendus et un contexte par défaut pour le résumé.
"""

import json
import os
import threading
import uuid
from pathlib import Path

from .paths import CONFIG_FILE

BACKUP_FILE = CONFIG_FILE.with_name("config.backup.json")
BROKEN_FILE = CONFIG_FILE.with_name("config.broken.json")

# channel_name / notion_title : libellés affichés dans l'onglet, mémorisés au moment du choix
PROFILE_FIELDS = ("name", "bot_id", "guild_id", "channel_id", "channel_name", "notion_page", "notion_title", "context")

_lock = threading.Lock()


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _blank_profile(name: str, bot_id: str) -> dict:
    return {"id": _new_id(), "name": name, "bot_id": bot_id, **dict.fromkeys(PROFILE_FIELDS[2:], "")}


def _load(path: Path) -> dict:
    """Contenu d'un config.json, complété au besoin ; ValueError s'il est illisible."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(data.get(k, []), list) for k in ("bots", "profiles")):
        raise ValueError("structure inattendue")
    bots = [b for b in data.get("bots", []) if isinstance(b, dict) and b.get("id") and b.get("token")]
    for b in bots:
        b.setdefault("name", "Bot")
    profiles = [{**_blank_profile("Profil", ""), **p} for p in data.get("profiles", [])
                if isinstance(p, dict) and p.get("id")]
    return {"bots": bots, "profiles": profiles}


def _read() -> dict:
    """Configuration actuelle. Un fichier abîmé (modifié à la main, disque plein…) est mis de côté dans
    config.broken.json et remplacé par sa copie de secours (config.backup.json) : l'app continue."""
    if not CONFIG_FILE.exists():
        return {"bots": [], "profiles": []}
    try:
        return _load(CONFIG_FILE)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        CONFIG_FILE.replace(BROKEN_FILE)
        try:
            data, origin = _load(BACKUP_FILE), "copie de secours reprise"
        except (OSError, UnicodeDecodeError, ValueError):
            data, origin = {"bots": [], "profiles": []}, "aucune sauvegarde valide, configuration vide"
        print(f"[config] {CONFIG_FILE.name} illisible ({e}) : copié dans {BROKEN_FILE.name}, {origin}.")
        _write(data)
        return data


def _write(data: dict) -> None:
    """Écriture atomique, puis copie de secours identique (reprise si config.json est abîmé)."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    for path in (CONFIG_FILE, BACKUP_FILE):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)


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
