"""Serveur local du widget : sert la page, pilote les bots Discord, transcrit et résume."""

import hmac
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .paths import ENV_FILE, STATIC_DIR


def load_env_file(path: Path) -> None:
    """Charge les variables d'un fichier .env (sans écraser celles déjà définies)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env_file(ENV_FILE)

# Sous Windows, mimetypes lit les types de fichiers dans le registre au premier envoi d'un
# fichier, ce qui peut prendre des dizaines de secondes. La table intégrée suffit.
if os.name == "nt":
    mimetypes._mimetypes_read_windows = None  # type: ignore[attr-defined]
    mimetypes._winreg = None  # type: ignore[attr-defined]
    mimetypes.init()

from . import bots, config, discord_sessions, notion, transcriber  # noqa: E402  (après le .env)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Charge (et télécharge au premier lancement) le modèle Whisper avant la 1re requête,
    # puis lance un processus par bot Discord configuré.
    transcriber.get_model()
    bots.start_all(os.getenv("TRANSCRIPT_SERVER", "http://127.0.0.1:8010"))
    yield
    bots.stop_all()


app = FastAPI(title="Discord Transcript", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Clé d'accès : dès que l'app est publiée (ex. intégrée dans Notion), la page s'ouvre
# avec ?key=… et chaque appel d'API la renvoie dans l'en-tête X-Access-Key.
ACCESS_KEY = os.getenv("TRANSCRIPT_ACCESS_KEY", "")


@app.middleware("http")
async def check_access_key(request: Request, call_next):
    if ACCESS_KEY and not request.url.path.startswith("/static/"):
        given = request.headers.get("x-access-key") or request.query_params.get("key", "")
        if not hmac.compare_digest(given.encode(), ACCESS_KEY.encode()):
            return PlainTextResponse("Accès refusé : ouvre le lien complet fourni (avec ?key=…).", status_code=401)
    response = await call_next(request)
    # Le navigateur (ou la fenêtre de l'app) revérifie chaque fichier : une mise à jour de la page
    # s'affiche tout de suite au lieu d'une ancienne version gardée en cache.
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


def _bot(bot_id: str, path: str, payload: dict | None = None):
    try:
        return bots.call(bot_id, path, payload)
    except bots.BotError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# --- Réunions Discord (appelé par les bots) -----------------------------------------
class DiscordSessionRequest(BaseModel):
    bot_id: str
    guild: str
    guild_id: str
    channel: str
    channel_id: str
    profile_id: str = ""  # vide : enregistrement lancé par /enregistrer dans Discord


def _session_or_404(session_id: str) -> discord_sessions.Session:
    session = discord_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session inconnue.")
    return session


@app.post("/api/discord/sessions")
def discord_start(req: DiscordSessionRequest) -> dict:
    profile_id = req.profile_id or config.match_profile(req.bot_id, req.guild_id, req.channel_id)
    return discord_sessions.start(req.bot_id, profile_id, req.guild, req.channel).to_dict()


@app.post("/api/discord/sessions/{session_id}/utterances", status_code=202)
def discord_utterance(
    session_id: str,
    audio: UploadFile = File(...),
    sample_rate: int = Form(...),
    user_id: str = Form(...),
    speaker: str = Form(...),
    start_ms: int = Form(...),
) -> dict:
    session = _session_or_404(session_id)
    discord_sessions.add_utterance(session, audio.file.read(), sample_rate, user_id, speaker, start_ms)
    return {"queued": True}


@app.post("/api/discord/sessions/{session_id}/stop")
def discord_stop(session_id: str) -> dict:
    session = _session_or_404(session_id)
    discord_sessions.stop(session)
    return session.to_dict()


# --- Réunions (affichées dans l'onglet de leur profil) -----------------------------
@app.get("/api/sessions")
def sessions_list() -> list[dict]:
    return discord_sessions.overviews()


@app.get("/api/sessions/{session_id}")
def session_detail(session_id: str) -> dict:
    return _session_or_404(session_id).to_dict()


class SessionUpdate(BaseModel):
    transcript: str


@app.patch("/api/sessions/{session_id}")
def session_update(session_id: str, req: SessionUpdate) -> dict:
    session = _session_or_404(session_id)
    if session.active or session.pending:
        raise HTTPException(status_code=409, detail="Réunion en cours : transcription non modifiable.")
    discord_sessions.update_transcript(session, req.transcript)
    return {"edited": bool(session.transcript_edit)}


@app.post("/api/sessions/{session_id}/summarize")
def session_summarize(session_id: str) -> StreamingResponse:
    session = _session_or_404(session_id)
    if not session.text().strip():
        raise HTTPException(status_code=400, detail="Transcription vide.")
    return StreamingResponse(discord_sessions.stream_summary(session), media_type="text/plain; charset=utf-8")


@app.post("/api/sessions/{session_id}/notion")
def session_notion(session_id: str) -> dict:
    session = _session_or_404(session_id)
    try:
        return {"url": discord_sessions.publish_notion(session)}
    except notion.NotionError as e:
        raise HTTPException(status_code=502, detail=f"Notion : {e}") from e


@app.post("/api/sessions/{session_id}/stop")
def session_stop_recording(session_id: str) -> dict:
    """« Terminer » : demande au bot de la réunion de quitter le salon."""
    session = _session_or_404(session_id)
    return _bot(session.bot_id, "/stop", {"session_id": session.id})


# --- Profils (un onglet chacun) ---------------------------------------------------------
class ProfileCreate(BaseModel):
    name: str = "Nouveau profil"


class ProfileUpdate(BaseModel):
    name: str | None = None
    bot_id: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    notion_page: str | None = None
    notion_title: str | None = None
    context: str | None = None


def _profile_or_404(profile_id: str) -> dict:
    profile = config.profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profil inconnu.")
    return profile


@app.get("/api/profiles")
def profiles_list() -> list[dict]:
    return config.profiles()


@app.post("/api/profiles")
def profile_create(req: ProfileCreate) -> dict:
    return config.create_profile(req.name.strip() or "Nouveau profil")


@app.patch("/api/profiles/{profile_id}")
def profile_update(profile_id: str, req: ProfileUpdate) -> dict:
    _profile_or_404(profile_id)
    return config.update_profile(profile_id, req.model_dump(exclude_none=True))


@app.delete("/api/profiles/{profile_id}")
def profile_delete(profile_id: str) -> dict:
    _profile_or_404(profile_id)
    config.delete_profile(profile_id)
    return {"deleted": profile_id}


@app.post("/api/profiles/{profile_id}/record")
def profile_record(profile_id: str) -> dict:
    """« Enregistrer » : le bot du profil rejoint son salon vocal."""
    profile = _profile_or_404(profile_id)
    if not profile["channel_id"]:
        raise HTTPException(status_code=400, detail="Choisis d'abord le salon vocal du profil (✎ Modifier).")
    return _bot(profile["bot_id"], "/record", {"channel_id": profile["channel_id"], "profile_id": profile_id})


# --- Bots et Notion (listes de choix des profils) ----------------------------------------
@app.get("/api/bots")
def bots_list() -> list[dict]:
    return bots.statuses()


@app.get("/api/bots/{bot_id}/channels")
def bot_channels(bot_id: str) -> list:
    return _bot(bot_id, "/channels")


@app.get("/api/notion/status")
def notion_status() -> dict:
    return {"configured": notion.configured()}


@app.get("/api/notion/pages")
def notion_pages() -> list[dict]:
    try:
        return notion.search_pages()
    except notion.NotionError as e:
        raise HTTPException(status_code=502, detail=f"Notion : {e}") from e
