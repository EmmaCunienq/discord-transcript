"""Publication du compte rendu dans une page Notion (API officielle).

Crée, sous la page indiquée, une nouvelle page « Réunion du <date> » contenant un
titre déroulant « Transcription », puis le résumé de la réunion.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
BATCH = 100       # blocs enfants maximum par requête
MAX_TEXT = 2000   # caractères maximum par élément de texte
MAX_RICH = 100    # éléments de texte maximum par bloc

MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
          "août", "septembre", "octobre", "novembre", "décembre"]

# Pages de compte rendu créées par l'app : « 🎙️ Réunion du 15 octobre 2026 »
MEETING_EMOJI = "🎙️"
MEETING_TITLE = re.compile(r"^Réunion du \d{1,2} \w+ \d{4}$")

# Ligne de transcription Discord : « [00:12] Marc : texte »
SPEAKER_LINE = re.compile(r"^(\[[\d:]+\])\s+(.{1,80}?) : (.*)$")


class NotionError(Exception):
    pass


def configured() -> bool:
    return bool(os.getenv("NOTION_TOKEN"))


def page_id(ref: str) -> str:
    """Accepte l'URL ou l'ID d'une page Notion et renvoie son ID."""
    path = ref.strip().split("?")[0].split("#")[0].replace("-", "")
    runs = re.findall(r"[0-9a-fA-F]{32,}", path)
    if not runs:
        raise NotionError("lien de page Notion invalide.")
    raw = runs[-1][-32:].lower()
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def meeting_title(timestamp: float | None = None) -> str:
    t = time.localtime(timestamp)
    return f"Réunion du {t.tm_mday} {MONTHS[t.tm_mon - 1]} {t.tm_year}"


# --- Conversion en blocs Notion ------------------------------------------------
def _text(content: str, **annotations) -> list[dict]:
    return [
        {"type": "text", "text": {"content": content[i:i + MAX_TEXT]}, "annotations": annotations}
        for i in range(0, len(content), MAX_TEXT)
    ]


def _rich(markdown: str) -> list[dict]:
    """Texte avec **gras** et `code` en ligne."""
    out = []
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", markdown):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out += _text(part[2:-2], bold=True)
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            out += _text(part[1:-1], code=True)
        else:
            out += _text(part)
    return out[:MAX_RICH]


def _block(kind: str, rich_text: list[dict], **extra) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text, **extra}}


def transcript_blocks(transcript: str) -> list[dict]:
    blocks = []
    for line in transcript.splitlines():
        if not line.strip():
            continue
        m = SPEAKER_LINE.match(line.strip())
        if m:
            rich = _text(m[1] + " ", color="gray") + _text(m[2], bold=True) + _text(" : " + m[3])
            blocks.append(_block("paragraph", rich[:MAX_RICH]))
        else:
            blocks.append(_block("paragraph", _rich(line.strip())))
    return blocks or [_block("paragraph", _text("(transcription vide)", italic=True))]


def summary_blocks(markdown: str) -> list[dict]:
    blocks = []
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip() or re.fullmatch(r"\s*-{3,}\s*", line):
            continue
        if m := re.match(r"^(#{1,3})\s+(.*)", line):
            if len(m[1]) <= 2:
                blocks.append(_block("heading_3", _rich(m[2])))
            else:
                blocks.append(_block("paragraph", _text(m[2], bold=True)))
        elif m := re.match(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)", line):
            blocks.append(_block("to_do", _rich(m[2]), checked=bool(m[1].strip())))
        elif m := re.match(r"^\s*[-*]\s+(.*)", line):
            blocks.append(_block("bulleted_list_item", _rich(m[1])))
        elif m := re.match(r"^\s*\d+[.)]\s+(.*)", line):
            blocks.append(_block("numbered_list_item", _rich(m[1])))
        elif m := re.match(r"^>\s?(.*)", line):
            blocks.append(_block("quote", _rich(m[1])))
        else:
            blocks.append(_block("paragraph", _rich(line.strip())))
    return blocks


# --- API ---------------------------------------------------------------------------
def _request(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {os.getenv('NOTION_TOKEN', '')}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        try:
            message = json.load(e).get("message", "")
        except ValueError:
            message = ""
        if e.code == 401:
            raise NotionError("jeton Notion invalide (vérifie NOTION_TOKEN).") from e
        if e.code == 404:
            raise NotionError("page introuvable : connecte l'intégration à la page "
                              "(menu ••• de la page → Connexions).") from e
        raise NotionError(f"erreur Notion {e.code} : {message}") from e
    except urllib.error.URLError as e:
        raise NotionError(f"impossible de joindre Notion ({e.reason}).") from e


def search_pages(limit: int = 300) -> list[dict]:
    """Pages partagées avec l'intégration (celles où elle peut créer un compte rendu)."""
    pages, cursor = [], None
    while len(pages) < limit:
        body = {"filter": {"property": "object", "value": "page"}, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = _request("POST", "/search", body)
        for page in res["results"]:
            if page.get("archived") or page.get("in_trash"):
                continue
            title_prop = next((p for p in page["properties"].values() if p["type"] == "title"), None)
            title = "".join(t["plain_text"] for t in title_prop["title"]) if title_prop else ""
            icon = page.get("icon") or {}
            emoji = icon.get("emoji", "") if icon.get("type") == "emoji" else ""
            if emoji == MEETING_EMOJI and MEETING_TITLE.match(title):
                continue  # compte rendu créé par l'app, pas une page de destination
            pages.append({"id": page["id"], "title": title or "Sans titre", "emoji": emoji})
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]
    return sorted(pages, key=lambda p: p["title"].lower())


def _append(block_id: str, children: list[dict]) -> list[dict]:
    created = []
    for i in range(0, len(children), BATCH):
        created += _request("PATCH", f"/blocks/{block_id}/children", {"children": children[i:i + BATCH]})["results"]
    return created


def publish(page_ref: str, transcript: str, summary: str, title: str) -> str:
    """Crée la page de la réunion sous la page indiquée et renvoie son lien."""
    if not configured():
        raise NotionError("NOTION_TOKEN non défini.")
    pid = page_id(page_ref)
    lines = transcript_blocks(transcript)
    summary = summary_blocks(summary)

    # Une requête accepte 100 enfants par bloc : on crée la page avec le début de la
    # transcription et du résumé, puis on complète.
    toggle = _block("heading_3", _text("Transcription"), is_toggleable=True, children=lines[:BATCH])
    page = _request("POST", "/pages", {
        "parent": {"page_id": pid},
        "icon": {"type": "emoji", "emoji": MEETING_EMOJI},
        "properties": {"title": {"title": _text(title)}},
        "children": [toggle] + summary[:BATCH - 1],
    })
    new_id = page["id"]
    if len(lines) > BATCH:
        toggle_id = _request("GET", f"/blocks/{new_id}/children?page_size=1")["results"][0]["id"]
        _append(toggle_id, lines[BATCH:])
    if len(summary) > BATCH - 1:
        _append(new_id, summary[BATCH - 1:])
    return page.get("url") or f"https://www.notion.so/{new_id.replace('-', '')}"
