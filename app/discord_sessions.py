"""Réunions Discord : le bot envoie une piste par participant, transcrite au fil de l'eau.

Chaque prise de parole arrive séparément avec le nom de son auteur : l'attribution est
exacte et deux personnes qui parlent en même temps sont transcrites toutes les deux.

Chaque réunion est enregistrée dans output/sessions/<id>/ et rechargée au démarrage :
la page l'affiche dans l'onglet de son profil (transcription corrigée, résumé, lien Notion compris).
"""

import json
import queue
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config, notion, summarizer, transcriber
from .paths import SESSIONS_DIR

# Deux interventions consécutives d'une même personne, séparées de moins de MERGE_GAP_SEC
# sans que personne d'autre ne parle entre-temps, sont regroupées sur une seule ligne.
MERGE_GAP_SEC = 3.0

# Phrases que Whisper « entend » parfois dans un bruit ou un souffle (héritées de sous-titres)
HALLUCINATION = re.compile(r"amara\.org|sous-titr|merci d'avoir regardé|abonnez-vous", re.IGNORECASE)


@dataclass
class Session:
    id: str
    bot_id: str
    profile_id: str  # profil (onglet) de la réunion ; vide si aucun ne correspond
    guild: str
    channel: str
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    segments: list[dict] = field(default_factory=list)
    participants: list[str] = field(default_factory=list)  # personnes présentes dans le salon (signalées par le bot)
    pending: int = 0
    last_text: dict[str, str] = field(default_factory=dict)  # user_id -> fin de sa dernière phrase
    transcript_edit: str = ""  # transcription corrigée à la main ("" = celle générée)
    # Fin de réunion : résumé puis publication dans Notion
    context: str = ""
    notion_page: str = ""
    finishing: bool = False
    summary: str = ""
    summary_status: str = ""  # "" | running | done | error
    summary_error: str = ""
    notion_status: str = ""   # "" | running | done | error | off
    notion_url: str = ""
    notion_error: str = ""

    @property
    def active(self) -> bool:
        return self.ended_at is None

    def speakers(self) -> list[str]:
        return sorted({s["speaker"] for s in self.segments})

    def transcript(self) -> str:
        lines: list[dict] = []
        for seg in sorted(self.segments, key=lambda s: s["start"]):
            prev = lines[-1] if lines else None
            if prev and prev["speaker"] == seg["speaker"] and seg["start"] - prev["end"] < MERGE_GAP_SEC:
                prev["text"] += " " + seg["text"]
                prev["end"] = max(prev["end"], seg["end"])
            else:
                lines.append(dict(seg))
        return "\n".join(f"[{_clock(l['start'])}] {l['speaker']} : {l['text']}" for l in lines)

    def text(self) -> str:
        """Transcription à utiliser : la version corrigée à la main s'il y en a une."""
        return self.transcript_edit or self.transcript()

    def overview(self) -> dict:
        """Infos d'un onglet (sans la transcription)."""
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "guild": self.guild,
            "channel": self.channel,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "elapsed": round((self.ended_at or time.time()) - self.started_at, 1),
            "active": self.active,
            "pending": self.pending,
            "speakers": self.speakers(),
            "participants": self.participants,
            "summary_status": self.summary_status,
            "notion_status": self.notion_status,
        }

    def to_dict(self) -> dict:
        return {
            **self.overview(),
            "transcript": self.text(),
            "edited": bool(self.transcript_edit),
            "summary": self.summary,
            "summary_error": self.summary_error,
            "notion_page": self.notion_page,
            "notion_url": self.notion_url,
            "notion_error": self.notion_error,
        }

    def save(self) -> None:
        """Enregistre la réunion ; appelé depuis plusieurs threads (transcription, page, fin de réunion)."""
        folder = SESSIONS_DIR / self.id
        with _save_lock:
            folder.mkdir(parents=True, exist_ok=True)
            data = {**self.to_dict(), "bot_id": self.bot_id, "transcript_edit": self.transcript_edit,
                    "context": self.context, "segments": sorted(self.segments, key=lambda s: s["start"])}
            _write(folder / "transcript.json", json.dumps(data, ensure_ascii=False, indent=2))
            header = f"# Réunion Discord — {self.guild} / {self.channel}\n\n" \
                     f"{time.strftime('%d/%m/%Y %H:%M', time.localtime(self.started_at))}\n\n"
            _write(folder / "transcript.md", header + self.text() + "\n")
            if self.summary:
                _write(folder / "resume.md", self.summary + "\n")


def _write(path: Path, text: str) -> None:
    """Écriture atomique : une coupure en pleine sauvegarde ne laisse jamais un fichier tronqué."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _clock(seconds: float) -> str:
    s = int(seconds)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


_sessions: dict[str, Session] = {}
_lock = threading.Lock()
_save_lock = threading.Lock()
_jobs: queue.Queue = queue.Queue()


def start(bot_id: str, profile_id: str, guild: str, channel: str) -> Session:
    """Nouvelle réunion : page Notion et contexte repris du profil à cet instant."""
    profile = config.profile(profile_id) or {}
    with _lock:
        session_id = time.strftime("%Y%m%d-%H%M%S")
        while session_id in _sessions:  # deux bots qui démarrent dans la même seconde
            session_id += "b"
        session = _sessions[session_id] = Session(
            session_id, bot_id, profile_id, guild, channel,
            context=profile.get("context", ""), notion_page=profile.get("notion_page", ""),
        )
    session.save()
    return session


def get(session_id: str) -> Session | None:
    return _sessions.get(session_id)


def overviews() -> list[dict]:
    """Réunions en cours et passées, les plus récentes d'abord (sans les réunions vides)."""
    with _lock:
        sessions = [s for s in _sessions.values() if s.active or s.pending or s.segments]
    return [s.overview() for s in sorted(sessions, key=lambda s: s.started_at, reverse=True)]


def set_participants(session: Session, names: list[str]) -> None:
    """Personnes présentes dans le salon, envoyées par le bot à chaque arrivée ou départ."""
    if session.active:
        session.participants = sorted(names)


def update_transcript(session: Session, transcript: str) -> None:
    """Correction faite dans la page, une fois la réunion terminée."""
    # Identique à la transcription générée : rien à garder comme correction
    session.transcript_edit = "" if transcript.strip() == session.transcript().strip() else transcript
    session.save()


def stream_summary(session: Session) -> Iterator[str]:
    """Nouveau résumé (demandé depuis la page), enregistré dans la réunion une fois terminé."""
    parts = []
    try:
        for chunk in summarizer.generate(session.text(), session.context):
            parts.append(chunk)
            yield chunk
    except summarizer.SummaryError as e:
        yield f"\n\n**Erreur :** {e}"
        return
    session.summary, session.summary_status, session.summary_error = "".join(parts).strip(), "done", ""
    session.save()


def publish_notion(session: Session) -> str:
    """Envoie (à nouveau) le compte rendu dans Notion, sur la page de la réunion ou à défaut
    celle de son profil ; renvoie le lien de la page créée."""
    page = session.notion_page or (config.profile(session.profile_id) or {}).get("notion_page", "")
    if not page:
        raise notion.NotionError("aucune page Notion choisie pour ce profil (✎ Modifier).")
    url = notion.publish(page, session.text(), session.summary, notion.meeting_title(session.started_at))
    session.notion_url, session.notion_status, session.notion_error = url, "done", ""
    session.save()
    return url


def stop(session: Session) -> None:
    with _lock:
        if session.ended_at is None:
            session.ended_at = time.time()
        finish = _claim_finish(session)
    session.save()
    if finish:
        _start_finish(session)


def _claim_finish(session: Session) -> bool:
    """À appeler sous _lock : vrai une seule fois, quand la réunion est finie et tout transcrit.

    Le statut passe à « running » dans le même verrou, pour que la page ne croie jamais
    la session terminée entre la dernière transcription et le début du résumé.
    """
    if session.active or session.pending or session.finishing:
        return False
    session.finishing = True
    if session.segments:
        session.summary_status = "running"
    return True


def _start_finish(session: Session) -> None:
    if session.segments:
        threading.Thread(target=_finish, args=(session,), daemon=True, name="discord-finish").start()


def _finish(session: Session) -> None:
    """Fin de réunion : résumé (Ollama si OLLAMA_MODEL est défini, sinon Claude), puis page Notion."""
    transcript = session.text()
    publish = notion.configured() and bool(session.notion_page)
    try:
        summary = summarizer.summarize(transcript, session.context)
        error = ""
    except Exception as e:
        summary, error = "", str(e)
        print(f"[discord] résumé impossible : {e}")
    # Statut Notion fixé avant celui du résumé : la page voit toujours une étape en cours
    session.notion_status = "running" if publish else "off"
    session.summary, session.summary_error = summary, error
    session.summary_status = "error" if error else "done"
    session.save()
    if not publish:
        return
    try:
        session.notion_url = notion.publish(
            session.notion_page, transcript, summary, notion.meeting_title(session.started_at),
        )
        session.notion_status = "done"
    except Exception as e:
        session.notion_error = str(e)
        session.notion_status = "error"
        print(f"[discord] publication Notion impossible : {e}")
    session.save()


def add_utterance(session: Session, pcm: bytes, sample_rate: int, user_id: str, speaker: str, start_ms: int) -> None:
    """Met une prise de parole (PCM 16 bits mono) en file d'attente de transcription."""
    with _lock:
        session.pending += 1
    _jobs.put((session, pcm, sample_rate, user_id, speaker, start_ms / 1000))


def _worker() -> None:
    while True:
        session, pcm, sample_rate, user_id, speaker, start = _jobs.get()
        try:
            audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
            # Les noms des participants aident Whisper à les orthographier ; la fin de la
            # phrase précédente de la même personne donne de la continuité.
            names = ", ".join(sorted({s["speaker"] for s in session.segments} | {speaker}))
            prompt = f"Réunion entre {names}. {session.last_text.get(user_id, '')}".strip()
            segments = transcriber.transcribe_audio(audio, sample_rate, prompt)
            with _lock:
                for seg in segments:
                    if len(segments) == 1 and HALLUCINATION.search(seg["text"]):
                        continue
                    session.segments.append({
                        "speaker": speaker,
                        "user_id": user_id,
                        "start": round(start + seg["start"], 2),
                        "end": round(start + seg["end"], 2),
                        "text": seg["text"],
                    })
                if segments:
                    session.last_text[user_id] = segments[-1]["text"][-200:]
        except Exception as e:  # une prise de parole ratée ne doit pas bloquer la réunion
            print(f"[discord] transcription impossible pour {speaker} : {e}")
        finally:
            with _lock:
                session.pending -= 1
                finish = _claim_finish(session)
            session.save()
            if finish:
                _start_finish(session)


def _load(path: Path) -> Session | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("segments"):
        return None  # réunion vide (bot jamais connecté, personne n'a parlé)
    sid = data["id"]
    started = data.get("started_at") or time.mktime(time.strptime(sid, "%Y%m%d-%H%M%S"))
    session = Session(
        sid, data.get("bot_id", ""), data.get("profile_id", ""), data["guild"], data["channel"], started_at=started,
        # Une réunion encore « en cours » dans le fichier a été interrompue par la fermeture de l'app
        ended_at=data.get("ended_at") or started + data.get("elapsed", 0),
        segments=data["segments"], transcript_edit=data.get("transcript_edit", ""),
        context=data.get("context", ""), notion_page=data.get("notion_page", ""), finishing=True,
        summary=data.get("summary", ""), summary_status=data.get("summary_status", ""),
        summary_error=data.get("summary_error", ""), notion_status=data.get("notion_status", ""),
        notion_url=data.get("notion_url", ""), notion_error=data.get("notion_error", ""),
    )
    if session.summary_status == "running":
        session.summary_status, session.summary_error = "error", "rédaction interrompue (application fermée)."
    if session.notion_status == "running":
        session.notion_status, session.notion_error = "error", "envoi interrompu (application fermée)."
    return session


def _load_saved() -> None:
    for path in sorted(SESSIONS_DIR.glob("*/transcript.json")):
        try:
            session = _load(path)
        except (OSError, ValueError, KeyError) as e:
            print(f"[discord] réunion illisible ignorée ({path.parent.name}) : {e}")
            continue
        if session:
            _sessions[session.id] = session


_load_saved()
threading.Thread(target=_worker, daemon=True, name="discord-transcription").start()
