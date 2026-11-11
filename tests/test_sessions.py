"""Réunions : transcription au fil de l'eau, fin de réunion, résumé, Notion, rechargement depuis le disque.

Whisper, le modèle de résumé et Notion sont simulés.
"""

import json
import threading
import time

import numpy as np
import pytest

from app import discord_sessions, notion, summarizer, transcriber
from app.paths import SESSIONS_DIR


def wait_until(condition, timeout=5.0):
    deadline = time.time() + timeout
    while not condition():
        if time.time() > deadline:
            raise AssertionError("condition jamais atteinte")
        time.sleep(0.02)


def seg(speaker, start, end, text):
    return {"speaker": speaker, "user_id": speaker, "start": start, "end": end, "text": text}


@pytest.fixture
def profile(write_config):
    write_config(profiles=[{"id": "p1", "bot_id": "b1", "notion_page": "page-1", "context": "Équipe produit"}])
    return "p1"


def test_demarrage_reprend_page_notion_et_contexte_du_profil(profile):
    s = discord_sessions.start("b1", profile, "Studio", "Réu")
    assert (s.bot_id, s.profile_id, s.notion_page, s.context) == ("b1", "p1", "page-1", "Équipe produit")
    assert s.active
    assert (SESSIONS_DIR / s.id / "transcript.json").exists()


def test_deux_demarrages_dans_la_meme_seconde_ont_des_ids_differents():
    a = discord_sessions.start("b1", "", "S", "A")
    b = discord_sessions.start("b2", "", "S", "B")
    assert a.id != b.id


def test_transcription_regroupe_les_phrases_proches_d_une_meme_personne():
    s = discord_sessions.Session("x", "b", "p", "g", "c", segments=[
        seg("Marc", 0.0, 2.0, "Bonjour."),
        seg("Marc", 3.0, 4.0, "On commence."),       # moins de 3 s après : même ligne
        seg("Julie", 4.5, 6.0, "D'accord."),
        seg("Marc", 70.0, 72.0, "Autre sujet."),
    ])
    assert s.transcript() == (
        "[00:00] Marc : Bonjour. On commence.\n[00:04] Julie : D'accord.\n[01:10] Marc : Autre sujet."
    )
    assert s.speakers() == ["Julie", "Marc"]


def test_sauvegardes_simultanees_sans_fichier_corrompu():
    """Transcription, page et fin de réunion enregistrent en même temps : le fichier reste lisible."""
    s = discord_sessions.start("b1", "", "Studio", "Réu")
    errors = []

    def save_many(n):
        try:
            for i in range(20):
                s.segments.append(seg(f"P{n}", n * 100 + i, n * 100 + i + 0.5, "Texte."))
                s.save()
        except Exception as e:  # remonté dans l'assertion ci-dessous
            errors.append(e)

    threads = [threading.Thread(target=save_many, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    s.save()
    assert errors == []
    folder = SESSIONS_DIR / s.id
    assert len(json.loads((folder / "transcript.json").read_text(encoding="utf-8"))["segments"]) == 120
    assert not list(folder.glob("*.tmp"))


def test_horloge_au_dela_d_une_heure():
    assert discord_sessions._clock(3725) == "1:02:05"


def test_prises_de_parole_transcrites_et_hallucinations_ignorees(monkeypatch, no_background_finish):
    replies = iter([
        [{"start": 0.0, "end": 1.5, "text": "On valide le budget."}],
        [{"start": 0.0, "end": 1.0, "text": "Sous-titres réalisés par Amara.org"}],  # bruit pris pour du texte
    ])
    prompts = []

    def fake_transcribe(audio, sample_rate, prompt):
        prompts.append(prompt)
        assert audio.dtype == np.float32 and sample_rate == 48000
        return next(replies)

    monkeypatch.setattr(transcriber, "transcribe_audio", fake_transcribe)
    s = discord_sessions.start("b1", "", "Studio", "Réu")
    pcm = (np.zeros(4800, dtype="<i2")).tobytes()
    discord_sessions.add_utterance(s, pcm, 48000, "u1", "Marc", 2000)
    discord_sessions.add_utterance(s, pcm, 48000, "u1", "Marc", 9000)
    wait_until(lambda: s.pending == 0)

    assert s.transcript() == "[00:02] Marc : On valide le budget."
    assert prompts[0] == "Réunion entre Marc."
    assert "On valide le budget." in prompts[1]  # continuité avec la phrase précédente


def test_fin_de_reunion_attend_les_dernieres_transcriptions(monkeypatch, no_background_finish):
    s = discord_sessions.start("b1", "", "Studio", "Réu")
    s.segments.append(seg("Marc", 0, 1, "Bonjour."))
    s.pending = 1
    discord_sessions.stop(s)
    assert not s.active and no_background_finish == []  # une prise de parole encore en cours
    with discord_sessions._lock:
        s.pending = 0
        assert discord_sessions._claim_finish(s)
        assert s.summary_status == "running"
        assert not discord_sessions._claim_finish(s)  # une seule fois


def test_fin_de_reunion_resume_puis_publie_dans_notion(monkeypatch, profile):
    monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
    monkeypatch.setattr(summarizer, "summarize", lambda text, ctx: f"## En bref\n{ctx} : {text}")
    published = []
    monkeypatch.setattr(notion, "publish", lambda page, text, summary, title: published.append(page) or "https://n/1")
    s = discord_sessions.start("b1", profile, "Studio", "Réu")
    s.segments.append(seg("Marc", 0, 1, "Bonjour."))
    discord_sessions._finish(s)

    assert s.summary == "## En bref\nÉquipe produit : [00:00] Marc : Bonjour."
    assert (s.summary_status, s.notion_status, s.notion_url) == ("done", "done", "https://n/1")
    assert published == ["page-1"]
    assert (SESSIONS_DIR / s.id / "resume.md").read_text(encoding="utf-8").startswith("## En bref")


def test_fin_de_reunion_sans_page_notion(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
    monkeypatch.setattr(summarizer, "summarize", lambda text, ctx: "résumé")
    s = discord_sessions.start("b1", "", "Studio", "Réu")
    s.segments.append(seg("Marc", 0, 1, "Bonjour."))
    discord_sessions._finish(s)
    assert s.notion_status == "off"


def test_fin_de_reunion_resume_en_echec(monkeypatch):
    def fail(text, ctx):
        raise summarizer.SummaryError("Ollama injoignable")
    monkeypatch.setattr(summarizer, "summarize", fail)
    s = discord_sessions.start("b1", "", "Studio", "Réu")
    s.segments.append(seg("Marc", 0, 1, "Bonjour."))
    discord_sessions._finish(s)
    assert (s.summary_status, s.summary_error) == ("error", "Ollama injoignable")


def test_liste_des_reunions_sans_les_reunions_vides():
    vide = discord_sessions.start("b1", "", "S", "Vide")
    vide.ended_at = time.time()
    ancienne = discord_sessions.start("b1", "", "S", "Ancienne")
    ancienne.started_at -= 3600
    ancienne.ended_at = time.time()
    ancienne.segments.append(seg("Marc", 0, 1, "x"))
    discord_sessions.start("b1", "p1", "S", "En cours")

    overviews = discord_sessions.overviews()
    assert [o["channel"] for o in overviews] == ["En cours", "Ancienne"]
    assert overviews[0]["profile_id"] == "p1" and overviews[0]["active"]
    assert "transcript" not in overviews[0]


def test_correction_de_transcription():
    s = discord_sessions.start("b1", "", "S", "C")
    s.segments.append(seg("Marc", 0, 1, "Bonjour."))
    discord_sessions.update_transcript(s, "[00:00] Marc : Bonjour à tous.")
    assert s.text() == "[00:00] Marc : Bonjour à tous." and s.to_dict()["edited"]
    # Revenir au texte d'origine efface la correction
    discord_sessions.update_transcript(s, s.transcript() + "\n")
    assert s.transcript_edit == "" and not s.to_dict()["edited"]


def test_resume_a_la_demande_enregistre_dans_la_reunion(monkeypatch):
    monkeypatch.setattr(summarizer, "generate", lambda text, ctx: iter(["## En", " bref\n", "Ok."]))
    s = discord_sessions.start("b1", "", "S", "C")
    s.summary_status = "error"
    chunks = list(discord_sessions.stream_summary(s))
    assert "".join(chunks) == "## En bref\nOk."
    assert (s.summary, s.summary_status) == ("## En bref\nOk.", "done")


def test_resume_a_la_demande_en_echec_garde_l_ancien(monkeypatch):
    def fail(text, ctx):
        yield "début"
        raise summarizer.SummaryError("modèle absent")
    monkeypatch.setattr(summarizer, "generate", fail)
    s = discord_sessions.start("b1", "", "S", "C")
    s.summary = "ancien"
    out = "".join(discord_sessions.stream_summary(s))
    assert out.endswith("**Erreur :** modèle absent")
    assert s.summary == "ancien"


def test_envoi_notion_manuel_utilise_la_page_du_profil_a_defaut(monkeypatch, profile):
    pages = []
    monkeypatch.setattr(notion, "publish", lambda page, text, summary, title: pages.append(page) or "https://n/2")
    s = discord_sessions.start("b1", profile, "S", "C")
    s.notion_page = ""  # réunion lancée avant le choix de la page
    assert discord_sessions.publish_notion(s) == "https://n/2"
    assert pages == ["page-1"] and s.notion_status == "done"


def test_envoi_notion_manuel_sans_page():
    s = discord_sessions.start("b1", "", "S", "C")
    with pytest.raises(notion.NotionError):
        discord_sessions.publish_notion(s)


def test_rechargement_depuis_le_disque(monkeypatch, no_background_finish):
    s = discord_sessions.start("b1", "p1", "Studio", "Réu")
    s.segments.append(seg("Marc", 0, 1, "Bonjour."))
    s.transcript_edit = "[00:00] Marc : Bonjour à tous."
    s.summary, s.summary_status = "résumé", "running"  # app fermée pendant la rédaction
    s.save()

    discord_sessions._sessions.clear()
    discord_sessions._load_saved()
    [loaded] = discord_sessions._sessions.values()
    assert (loaded.bot_id, loaded.profile_id, loaded.channel) == ("b1", "p1", "Réu")
    assert loaded.text() == "[00:00] Marc : Bonjour à tous."
    assert loaded.ended_at is not None and not loaded.active
    assert loaded.summary_status == "error" and "interrompue" in loaded.summary_error
    assert loaded.finishing  # pas de nouvelle rédaction automatique au redémarrage


def test_rechargement_d_un_ancien_fichier_et_fichiers_illisibles():
    ancien = SESSIONS_DIR / "20261108-165256"
    ancien.mkdir(parents=True)
    (ancien / "transcript.json").write_text(json.dumps({
        "id": "20261108-165256", "guild": "Studio", "channel": "Réu", "elapsed": 60.0, "ended_at": None,
        "segments": [seg("Emma", 0, 1, "Test.")],
    }), encoding="utf-8")
    vide = SESSIONS_DIR / "20261108-170000"
    vide.mkdir()
    (vide / "transcript.json").write_text(json.dumps({"id": "20261108-170000", "guild": "S", "channel": "C",
                                                      "segments": []}), encoding="utf-8")
    casse = SESSIONS_DIR / "casse"
    casse.mkdir()
    (casse / "transcript.json").write_text("{pas du json", encoding="utf-8")

    discord_sessions._load_saved()
    assert list(discord_sessions._sessions) == ["20261108-165256"]
    s = discord_sessions._sessions["20261108-165256"]
    assert (s.bot_id, s.profile_id) == ("", "")
    assert s.ended_at - s.started_at == pytest.approx(60.0)
