"""Fichier .env (lu par le serveur et le lanceur de bureau, sans effet de bord) et environnement
transmis aux programmes lancés par l'app."""

import os
from pathlib import Path

# Clés dont les bots Discord et Ollama n'ont pas besoin : elles ne leur sont pas transmises
SECRET_KEYS = {"ANTHROPIC_API_KEY", "NOTION_TOKEN", "DISCORD_TOKEN", "TRANSCRIPT_ACCESS_KEY"}


def child_env(**extra: str) -> dict[str, str]:
    """Environnement d'un programme lancé par l'app : le nôtre sans les secrets, plus `extra`."""
    return {**{k: v for k, v in os.environ.items() if k.upper() not in SECRET_KEYS}, **extra}


def read(path: Path) -> dict[str, str]:
    """Clés et valeurs du fichier (lignes `CLÉ=valeur`, commentaires `#` ignorés)."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                values[key.strip()] = value.strip().strip("\"'")
    return values
