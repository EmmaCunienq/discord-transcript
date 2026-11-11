"""API du serveur (utilisée par la page et par les bots). Les bots Discord sont simulés."""

import pytest
from fastapi.testclient import TestClient

from app import bots, discord_sessions, notion, server, summarizer
from tests.test_sessions import wait_until


@pytest.fixture
def client():
    # Sans « with » : les événements de démarrage (modèle Whisper, bots) ne sont pas lancés
    return TestClient(server.app)


@pytest.fixture
def bot_calls(monkeypatch):
    """Remplace les appels aux bots ; `responses[path]` donne la réponse (ou l'exception) à renvoyer."""
    calls, responses = [], {}

    def fake_call(bot_id, path, payload=None):
        calls.append((bot_id, path, payload))
        result = responses.get(path, {"ok": True})
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(bots, "call", fake_call)
    return calls, responses


def test_page_servie_sans_cache(client):
    res = client.get("/")
    assert res.status_code == 200 and "Discord Transcript" in res.text
    assert res.headers["cache-control"] == "no-cache"


def test_cle_d_acces_quand_l_app_est_publiee(client, monkeypatch):
    monkeypatch.setattr(server, "ACCESS_KEY", "secret")
    assert client.get("/api/profiles").status_code == 401
    assert client.get("/api/profiles", headers={"X-Access-Key": "secret"}).status_code == 200
    assert client.get("/?key=secret").status_code == 200
    assert client.get("/static/settings.html").status_code == 200  # fichiers statiques libres


# --- Profils ------------------------------------------------------------------------
def test_cycle_de_vie_d_un_profil(client, write_config):
    write_config(bots=[{"id": "b1", "name": "Bot", "token": "t"}])
    created = client.post("/api/profiles", json={"name": "  "}).json()
    assert created["name"] == "Nouveau profil" and created["bot_id"] == "b1"

    pid = created["id"]
    updated = client.patch(f"/api/profiles/{pid}", json={"name": "Atelier", "channel_id": "c1",
                                                          "channel_name": "Studio · Atelier"}).json()
    assert (updated["name"], updated["channel_id"], updated["notion_page"]) == ("Atelier", "c1", "")
    assert [p["name"] for p in client.get("/api/profiles").json()] == ["Atelier"]

    assert client.delete(f"/api/profiles/{pid}").status_code == 200
    assert client.get("/api/profiles").json() == []
    assert client.patch(f"/api/profiles/{pid}", json={"name": "x"}).status_code == 404


def test_enregistrer_sans_salon(client, write_config, bot_calls):
    write_config(profiles=[{"id": "p1", "bot_id": "b1"}])
    res = client.post("/api/profiles/p1/record")
    assert res.status_code == 400 and "salon" in res.json()["detail"]
    assert bot_calls[0] == []


def test_enregistrer_demande_au_bot_du_profil(client, write_config, bot_calls):
    calls, responses = bot_calls
    responses["/record"] = {"session_id": "s1", "channel": "Réu"}
    write_config(profiles=[{"id": "p1", "bot_id": "b1", "channel_id": "c1"}])
    assert client.post("/api/profiles/p1/record").json() == {"session_id": "s1", "channel": "Réu"}
    assert calls == [("b1", "/record", {"channel_id": "c1", "profile_id": "p1"})]


def test_erreur_du_bot_transmise_a_la_page(client, write_config, bot_calls):
    bot_calls[1]["/record"] = bots.BotError(409, "Un enregistrement est déjà en cours sur ce serveur.")
    write_config(profiles=[{"id": "p1", "bot_id": "b1", "channel_id": "c1"}])
    res = client.post("/api/profiles/p1/record")
    assert res.status_code == 409 and res.json()["detail"].startswith("Un enregistrement")


def test_bots_et_salons(client, write_config, bot_calls):
    calls, responses = bot_calls
    write_config(bots=[{"id": "b1", "name": "Principal", "token": "t"}, {"id": "b2", "name": "Second", "token": "u"}])
    responses["/guilds"] = [{"id": "g1", "name": "Studio"}]
    responses["/channels"] = [{"id": "c1", "name": "Réu", "guild": "Studio", "guild_id": "g1", "people": []}]
    listed = client.get("/api/bots").json()
    assert [(b["name"], b["guilds"], b["error"]) for b in listed] == [
        ("Principal", [{"id": "g1", "name": "Studio"}], ""), ("Second", [{"id": "g1", "name": "Studio"}], ""),
    ]
    assert client.get("/api/bots/b2/channels").json()[0]["name"] == "Réu"
    assert calls[-1] == ("b2", "/channels", None)


def test_bot_en_erreur_dans_la_liste(client, write_config, monkeypatch):
    write_config(bots=[{"id": "b1", "name": "Principal", "token": "t"}])

    def down(bot_id, path, payload=None):
        raise bots.BotError(503, "Le bot s'est arrêté (token invalide ? voir le journal).")
    monkeypatch.setattr(bots, "call", down)
    [bot] = client.get("/api/bots").json()
    assert bot["guilds"] == [] and "token invalide" in bot["error"]


# --- Réunions -------------------------------------------------------------------------
def start_session(client, **extra):
    body = {"bot_id": "b1", "guild": "Studio", "guild_id": "g1", "channel": "Réu", "channel_id": "c1", **extra}
    return client.post("/api/discord/sessions", json=body).json()


def test_reunion_lancee_depuis_discord_rangee_dans_le_bon_profil(client, write_config):
    write_config(profiles=[{"id": "p1", "bot_id": "b1", "guild_id": "g1", "channel_id": "c1", "notion_page": "pg"}])
    s = start_session(client)
    assert s["profile_id"] == "p1" and s["notion_page"] == "pg"
    assert start_session(client, profile_id="p9")["profile_id"] == "p9"


def test_suivi_d_une_reunion_de_bout_en_bout(client, monkeypatch, no_background_finish, bot_calls):
    from app import transcriber
    monkeypatch.setattr(transcriber, "transcribe_audio",
                        lambda audio, sr, prompt: [{"start": 0.0, "end": 1.0, "text": "On valide."}])
    sid = start_session(client)["id"]
    res = client.post(f"/api/discord/sessions/{sid}/utterances", files={"audio": ("a.s16", b"\0\0" * 960)},
                      data={"sample_rate": "48000", "user_id": "u1", "speaker": "Marc", "start_ms": "1500"})
    assert res.status_code == 202
    session = discord_sessions.get(sid)
    wait_until(lambda: session.pending == 0)

    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["transcript"] == "[00:01] Marc : On valide." and detail["active"]
    assert client.patch(f"/api/sessions/{sid}", json={"transcript": "x"}).status_code == 409

    # « Terminer » dans la page : demande au bot de la réunion de quitter le salon
    client.post(f"/api/sessions/{sid}/stop")
    assert bot_calls[0][-1] == ("b1", "/stop", {"session_id": sid})
    # Le bot confirme l'arrêt
    assert client.post(f"/api/discord/sessions/{sid}/stop").json()["active"] is False
    assert no_background_finish == [sid]

    res = client.patch(f"/api/sessions/{sid}", json={"transcript": "[00:01] Marc : On valide le budget."})
    assert res.json() == {"edited": True}
    assert [s["id"] for s in client.get("/api/sessions").json()] == [sid]


def test_reunion_inconnue(client):
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.post("/api/discord/sessions/nope/stop").status_code == 404


def test_resume_a_la_demande_en_flux(client, monkeypatch):
    monkeypatch.setattr(summarizer, "generate", lambda text, ctx: iter(["## En bref\n", "Ok."]))
    s = discord_sessions.start("b1", "", "S", "C")
    assert client.post(f"/api/sessions/{s.id}/summarize").status_code == 400  # transcription vide
    s.segments.append({"speaker": "M", "user_id": "M", "start": 0, "end": 1, "text": "x"})
    res = client.post(f"/api/sessions/{s.id}/summarize")
    assert res.text == "## En bref\nOk." and s.summary == "## En bref\nOk."


def test_envoi_notion_en_erreur(client, monkeypatch):
    def fail(page, text, summary, title):
        raise notion.NotionError("page introuvable")
    monkeypatch.setattr(notion, "publish", fail)
    s = discord_sessions.start("b1", "", "S", "C")
    s.notion_page = "pg"
    res = client.post(f"/api/sessions/{s.id}/notion")
    assert res.status_code == 502 and res.json()["detail"] == "Notion : page introuvable"


def test_notion_statut_et_pages(client, monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "ntn")
    monkeypatch.setattr(notion, "search_pages", lambda: [{"id": "1", "title": "Réunions", "emoji": ""}])
    assert client.get("/api/notion/status").json() == {"configured": True}
    assert client.get("/api/notion/pages").json()[0]["title"] == "Réunions"
