"""Interface : la vraie page dans Microsoft Edge sans fenêtre, pilotée comme par un utilisateur.

Le serveur tourne dans ce processus ; les bots Discord, le résumé et Notion sont simulés
(aucun salon rejoint, aucune page créée). Ignoré si Edge n'est pas installé.
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from app import bots, config, discord_sessions, notion, server, summarizer, transcriber

pytestmark = pytest.mark.slow

EDGE_PATHS = [
    Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
]
EDGE = next((str(p) for p in EDGE_PATHS if p.is_file()), shutil.which("msedge"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Page:
    """Exécute du JavaScript dans la page via le protocole de débogage (CDP)."""

    def __init__(self, ws):
        self.ws, self.next_id = ws, 0

    def js(self, expression: str, timeout: float = 15):
        self.next_id += 1
        self.ws.send(json.dumps({"id": self.next_id, "method": "Runtime.evaluate", "params": {
            "expression": expression, "awaitPromise": True, "returnByValue": True}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv(timeout=timeout))
            if msg.get("id") == self.next_id:
                result = msg["result"]
                if "exceptionDetails" in result:
                    raise AssertionError(result["exceptionDetails"].get("exception", {}).get("description", result))
                return result["result"].get("value")
        raise TimeoutError(expression)

    def wait(self, condition: str, timeout: float = 10):
        """Attend qu'une expression JS devienne vraie (l'interface se met à jour par interrogation)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.js(f"!!({condition})"):
                return
            time.sleep(0.2)
        raise AssertionError(f"jamais vrai : {condition}\nstatut : {self.js('statusText.textContent')}, "
                             f"légende : {self.js('caption.textContent')}")

    def click(self, selector: str):
        self.js(f"document.querySelector({json.dumps(selector)}).click()")

    def tabs(self):
        return self.js("[...document.querySelectorAll('.profile-tab')].map(t => t.textContent"
                       " + (t.classList.contains('active') ? ' [actif]' : '') + (t.classList.contains('live') ? ' [live]' : ''))")


@pytest.fixture
def fakes(monkeypatch):
    """Bots, Whisper, résumé et Notion simulés ; renvoie le journal des appels aux bots."""
    calls = []

    def fake_bot(bot_id, path, payload=None):
        calls.append((bot_id, path, payload))
        if path == "/guilds":
            return [{"id": "g1", "name": "Studio"}]
        if path == "/channels":
            return [{"id": "c1", "name": "Réu", "guild": "Studio", "guild_id": "g1", "people": [], "recording": False},
                    {"id": "c2", "name": "Atelier", "guild": "Studio", "guild_id": "g1", "people": [], "recording": False}]
        if path == "/record":  # le bot rejoint le salon et ouvre la réunion
            channel = {"c1": "Réu", "c2": "Atelier"}[payload["channel_id"]]
            s = discord_sessions.start(bot_id, payload["profile_id"], "Studio", channel)
            return {"session_id": s.id, "channel": channel}
        if path == "/stop":
            discord_sessions.stop(discord_sessions.get(payload["session_id"]))
            return {"stopped": payload["session_id"]}
        raise AssertionError(path)

    monkeypatch.setattr(bots, "call", fake_bot)
    monkeypatch.setattr(bots, "start_all", lambda url: None)
    monkeypatch.setattr(transcriber, "get_model", lambda: None)
    monkeypatch.setattr(transcriber, "transcribe_audio",
                        lambda audio, sr, prompt: [{"start": 0.0, "end": 2.0, "text": "On valide le budget."}])
    monkeypatch.setattr(summarizer, "summarize", lambda text, ctx: "## En bref\nBudget validé.")
    monkeypatch.setattr(summarizer, "generate", lambda text, ctx: iter(["## En bref\n", "Résumé refait."]))
    monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
    monkeypatch.setattr(notion, "search_pages", lambda: [{"id": "pg-1", "title": "Réunions", "emoji": "📒"}])
    monkeypatch.setattr(notion, "publish", lambda page, text, summary, title: "https://notion.so/compte-rendu")
    return calls


@pytest.fixture
def page(fakes, write_config):
    if not EDGE:
        pytest.skip("Microsoft Edge introuvable")
    write_config(
        bots=[{"id": "b1", "name": "Principal", "token": "t"}],
        profiles=[
            {"id": "p1", "name": "Équipe", "bot_id": "b1", "guild_id": "g1", "channel_id": "c1",
             "channel_name": "Studio · Réu", "notion_page": "pg-1", "notion_title": "Réunions"},
            {"id": "p2", "name": "Atelier", "bot_id": "b1"},
        ],
    )
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)

    debug_port = free_port()
    profile_dir = tempfile.mkdtemp(prefix="dt-edge-")
    edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={debug_port}",
                             f"--user-data-dir={profile_dir}", "--no-first-run", "--window-size=600,1100",
                             f"http://127.0.0.1:{port}/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    from websockets.sync.client import connect
    try:
        deadline = time.time() + 20
        while True:
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json", timeout=2))
                target = next(t for t in targets if t["type"] == "page" and str(port) in t["url"])
                break
            except (OSError, StopIteration):
                if time.time() > deadline:
                    raise
                time.sleep(0.3)
        with connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
            p = Page(ws)
            p.wait("document.readyState === 'complete' && document.querySelectorAll('.profile-tab').length >= 3")
            yield p
    finally:
        edge.terminate()
        edge.wait(10)
        srv.should_exit = True
        shutil.rmtree(profile_dir, ignore_errors=True)


def test_onglets_de_profils_au_demarrage(page):
    assert page.tabs() == ["Équipe [actif]", "Atelier", "+Nouveau profil"]
    assert page.js("document.getElementById('profileSummary').textContent") == "🔊 Studio · Réu · 📄 Réunions"
    assert page.js("caption.textContent") == "Prêt à enregistrer Réu"
    assert page.js("recordBtn.disabled") is False
    assert page.js("notes.hidden") is True
    assert page.js("historyEmpty.hidden") is False


def test_enregistrement_complet_dans_un_profil(page, fakes):
    page.click("#recordBtn")
    page.wait("!stopControl.hidden")
    assert ("b1", "/record", {"channel_id": "c1", "profile_id": "p1"}) in fakes
    assert page.tabs()[0] == "Équipe [actif] [live]"
    assert page.js("widget.dataset.state") == "recording"
    assert page.js("recordControl.hidden") is True

    # Une prise de parole arrive pendant la réunion
    [session] = discord_sessions._sessions.values()
    discord_sessions.add_utterance(session, b"\0\0" * 960, 48000, "u1", "Marc", 1000)
    page.wait("transcript.value.includes('Marc : On valide le budget.')")
    assert page.js("transcript.readOnly") is True
    assert page.js("caption.textContent") == "🔊 Réu · 1 intervenant"

    # Terminer : le bot quitte le salon, le résumé est rédigé puis publié dans Notion
    page.click("#stopBtn")
    page.wait("summary.textContent.includes('Budget validé.')")
    assert ("b1", "/stop", {"session_id": session.id}) in fakes
    page.wait("notionStatus.textContent.includes('Voir dans Notion')")
    assert page.tabs()[0] == "Équipe [actif]"
    assert page.js("transcript.readOnly") is False
    assert page.js("document.querySelectorAll('#historyList li').length") == 1


def test_correction_et_resume_refait(page):
    s = discord_sessions.start("b1", "p1", "Studio", "Réu")
    s.segments.append({"speaker": "Marc", "user_id": "u1", "start": 0, "end": 1, "text": "Bonjour."})
    s.summary, s.summary_status = "## En bref\nAncien.", "done"
    discord_sessions.stop(s)
    page.wait("detail && detail.id === " + json.dumps(s.id))
    page.click("#tabTranscript")
    page.js("transcript.value = '[00:00] Marc : Bonjour à tous.'; transcript.dispatchEvent(new Event('input'))")
    deadline = time.time() + 5
    while s.transcript_edit != "[00:00] Marc : Bonjour à tous." and time.time() < deadline:
        time.sleep(0.1)
    assert s.transcript_edit == "[00:00] Marc : Bonjour à tous."
    page.wait("transcriptHint.textContent.startsWith('Corrigée')")

    assert page.js("summarizeLabel.textContent") == "Refaire le résumé"
    page.click("#summarizeBtn")
    page.wait("summary.textContent.includes('Résumé refait.')")
    assert s.summary == "## En bref\nRésumé refait."


def test_regler_un_profil_puis_enregistrer(page, fakes):
    page.click(".profile-tab:nth-child(2)")
    page.wait("caption.textContent === 'Choisis le salon vocal de ce profil (✎ Modifier)'")
    assert page.js("recordBtn.disabled") is True

    page.click("#editBtn")
    page.wait("[...pChannel.options].some(o => o.value === 'c2') && [...pNotion.options].some(o => o.value === 'pg-1')")
    assert page.js("pBot.selectedOptions[0].text") == "Principal"
    page.js("pChannel.value = 'c2'; pChannel.dispatchEvent(new Event('change'))")
    page.js("pNotion.value = 'pg-1'; pNotion.dispatchEvent(new Event('change'))")
    page.js("pName.value = 'Atelier du jeudi'; pName.dispatchEvent(new Event('input'))")
    deadline = time.time() + 5
    while config.profile("p2")["name"] != "Atelier du jeudi" and time.time() < deadline:
        time.sleep(0.1)
    saved = config.profile("p2")
    assert (saved["channel_id"], saved["guild_id"], saved["channel_name"]) == ("c2", "g1", "Studio · Atelier")
    assert (saved["notion_page"], saved["notion_title"]) == ("pg-1", "Réunions")

    page.click("#doneBtn")
    assert page.tabs()[1] == "Atelier du jeudi [actif]"
    page.wait("!recordBtn.disabled")
    page.click("#recordBtn")
    page.wait("!stopControl.hidden")
    assert ("b1", "/record", {"channel_id": "c2", "profile_id": "p2"}) in fakes
    # Le premier profil n'est pas touché
    assert page.tabs()[0] == "Équipe"


def test_deux_profils_enregistrent_en_meme_temps(page):
    a = discord_sessions.start("b1", "p1", "Studio", "Réu")
    b = discord_sessions.start("b2", "p2", "Studio", "Atelier")
    page.wait("document.querySelectorAll('.profile-tab.live').length === 2")
    page.click(".profile-tab:nth-child(2)")
    page.wait(f"detail && detail.id === {json.dumps(b.id)}")
    assert page.js("caption.textContent") == "🔊 Atelier · 0 intervenant"
    page.click(".profile-tab:nth-child(1)")
    page.wait(f"detail && detail.id === {json.dumps(a.id)}")


def test_historique_d_un_profil(page):
    old = discord_sessions.start("b1", "p1", "Studio", "Réu")
    old.started_at -= 86400
    old.segments.append({"speaker": "Julie", "user_id": "u2", "start": 0, "end": 1, "text": "Hier."})
    discord_sessions.stop(old)
    new = discord_sessions.start("b1", "p1", "Studio", "Réu")
    new.segments.append({"speaker": "Marc", "user_id": "u1", "start": 0, "end": 1, "text": "Aujourd'hui."})
    discord_sessions.stop(new)
    # Réunion sans profil connu : rangée dans le premier onglet
    orphan = discord_sessions.start("b9", "supprime", "Studio", "Réu")
    orphan.started_at -= 2 * 86400
    orphan.segments.append({"speaker": "Léa", "user_id": "u3", "start": 0, "end": 1, "text": "Avant."})
    discord_sessions.stop(orphan)

    page.wait("document.querySelectorAll('#historyList li').length === 3")
    page.wait("transcript.value.includes('Aujourd')")  # la dernière par défaut
    page.click("#historyList li:nth-child(2) button")
    page.wait("transcript.value.includes('Hier.')")
    assert page.js("!!document.getElementById('latestBtn')") is True
    page.click("#latestBtn")
    page.wait("transcript.value.includes('Aujourd')")
    # L'autre profil n'a aucune réunion
    page.click(".profile-tab:nth-child(2)")
    page.wait("!historyEmpty.hidden && notes.hidden")


def test_creer_et_supprimer_un_profil(page):
    page.click(".profile-tab.add")
    page.wait("document.querySelectorAll('.profile-tab').length === 4 && !profileEditor.hidden")
    assert page.tabs()[2] == "Nouveau profil [actif]"
    assert len(config.profiles()) == 3

    page.js("window.confirm = () => true")
    page.click("#deleteProfileBtn")
    page.wait("document.querySelectorAll('.profile-tab').length === 3")
    assert page.tabs()[0] == "Équipe [actif]"
    assert [p["name"] for p in config.profiles()] == ["Équipe", "Atelier"]


def test_page_de_reglages_hors_application_de_bureau(page):
    page.js("location.href = '/static/settings.html'")
    page.wait("location.pathname === '/static/settings.html' && document.getElementById('status')?.textContent", 15)
    assert "uniquement dans l'application de bureau" in page.js("document.getElementById('status').textContent")
    assert page.js("document.getElementById('saveBtn').disabled") is True
