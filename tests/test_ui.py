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
        result = self.command("Runtime.evaluate", {
            "expression": expression, "awaitPromise": True, "returnByValue": True}, timeout)
        if "exceptionDetails" in result:
            raise AssertionError(result["exceptionDetails"].get("exception", {}).get("description", result))
        return result["result"].get("value")

    def command(self, method: str, params: dict, timeout: float = 15):
        self.next_id += 1
        self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv(timeout=timeout))
            if msg.get("id") == self.next_id:
                assert "error" not in msg, msg
                return msg["result"]
        raise TimeoutError(method)

    def wait(self, condition: str, timeout: float = 10):
        """Attend qu'une expression JS devienne vraie (l'interface se met à jour par interrogation)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.js(f"!!({condition})"):
                return
            time.sleep(0.2)
        raise AssertionError(f"jamais vrai : {condition}\nstatut : "
                             f"{self.js('document.getElementById(\"status\")?.textContent')}")

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
    discord_sessions.set_participants(session, ["Marc", "Ana", "Léo"])
    discord_sessions.add_utterance(session, b"\0\0" * 960, 48000, "u1", "Marc", 1000)
    page.wait("transcript.value.includes('Marc : On valide le budget.')")
    assert page.js("transcript.readOnly") is True
    assert page.js("caption.textContent") == "🔊 Réu · 3 participants · 1 intervenant"

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
    assert page.js("caption.textContent") == "🔊 Atelier · 0 participant · 0 intervenant"
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
    assert page.js("document.getElementById('form').hidden") is True
    assert page.js("document.querySelectorAll('[name=design]').length") == 6


def test_menu_dans_la_page_et_navigation(page):
    page.click(".app-menu-trigger")
    assert page.js("document.querySelector('.app-menu').open") is True
    assert page.js("document.querySelector('.app-menu a[aria-current=page]').textContent") == "Accueil"
    assert page.js("[...document.querySelectorAll('[data-desktop-action]')].every(button => button.hidden)") is True
    page.click("#settingsLink")
    page.wait("location.pathname === '/static/settings.html' && !!document.querySelector('.app-menu')")
    page.click(".app-menu-trigger")
    assert page.js("document.querySelector('.app-menu a[aria-current=page]').textContent") == "Réglages"
    page.click(".app-menu [data-home-link]")
    page.wait("location.pathname === '/' && !!document.getElementById('widget')")


def test_menu_clavier_et_fermeture(page):
    page.js("document.querySelector('.app-menu-trigger').focus()")
    page.command("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "text": "\r", "windowsVirtualKeyCode": 13})
    page.command("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    assert page.js("document.querySelector('.app-menu').open") is True
    page.command("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
    assert page.js("document.querySelector('.app-menu').open") is False
    assert page.js("document.activeElement.classList.contains('app-menu-trigger')") is True
    page.click(".app-menu-trigger")
    page.js("document.querySelector('.brand h1').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
    assert page.js("document.querySelector('.app-menu').open") is False
    page.click(".app-menu-trigger")
    page.js("document.getElementById('editBtn').focus()")
    assert page.js("document.querySelector('.app-menu').open") is False


def test_menu_actions_de_bureau_et_erreurs(page):
    page.js("""window.menuCalls = [];
        window.pywebview = {api: {
            open_data_folder: async () => window.menuCalls.push('folder'),
            open_log_file: async () => { throw new Error('journal indisponible'); },
            quit_app: async () => window.menuCalls.push('quit'),
        }};
        window.dispatchEvent(new Event('pywebviewready'));
    """)
    for action in ("open_data_folder", "open_log_file", "quit_app"):
        assert page.js(f"document.querySelector('[data-desktop-action={action}]').hidden") is False
    page.click(".app-menu-trigger")
    page.click("[data-desktop-action=open_data_folder]")
    page.wait("!document.querySelector('.app-menu').open && window.menuCalls.includes('folder')")
    page.click(".app-menu-trigger")
    page.click("[data-desktop-action=open_log_file]")
    page.wait("!document.querySelector('.app-menu-error').hidden")
    assert page.js("document.querySelector('.app-menu').open") is True
    assert page.js("document.querySelector('[data-desktop-action=open_log_file]').disabled") is False
    page.js("window.pywebview.api.open_log_file = async () => window.menuCalls.push('log')")
    page.click("[data-desktop-action=open_log_file]")
    page.wait("!document.querySelector('.app-menu').open && window.menuCalls.includes('log')")
    page.click(".app-menu-trigger")
    page.click("[data-desktop-action=quit_app]")
    page.wait("window.menuCalls.includes('quit')")


def test_menu_assorti_aux_themes_sur_petit_ecran(page):
    page.command("Emulation.setDeviceMetricsOverride", {
        "width": 360, "height": 800, "deviceScaleFactor": 1, "mobile": False})
    page.click(".app-menu-trigger")
    assert page.js("document.querySelector('.app-menu-panel').getBoundingClientRect().left >= 0") is True
    page.click("#settingsLink")
    page.wait("document.querySelectorAll('[name=design]').length === 6")
    for design in ("basic", "woodland", "city", "mountain", "ocean", "japan"):
        page.click(f"input[value={design}]")
        page.click(".app-menu-trigger")
        assert page.js("getComputedStyle(document.querySelector('.app-menu-panel')).backgroundColor === getComputedStyle(document.querySelector('.settings-panel')).backgroundColor") is True
        assert page.js("document.querySelector('.app-menu-panel').getBoundingClientRect().left >= 0 && document.querySelector('.app-menu-panel').getBoundingClientRect().right <= innerWidth") is True
        page.click(".app-menu-trigger")


@pytest.mark.parametrize("design,scheme", [("basic", None), ("woodland", None), ("city", "dark"),
                                           ("mountain", "light"), ("ocean", "light"), ("japan", "dark")])
def test_themes_appliques_et_memorises_entre_les_pages(page, design, scheme):
    assert page.js("getComputedStyle(document.getElementById('settingsLink')).display") != "none"
    page.click("#settingsLink")
    page.wait("document.querySelectorAll('[name=design]').length === 6")
    if design == "basic":
        page.click("input[value=mountain]")
    page.click(f"input[value='{design}']")
    assert page.js("document.documentElement.dataset.design") == design
    assert page.js("localStorage.getItem('dt.design')") == design
    assert "mémorisé" in page.js("document.querySelector('[data-theme-notice]').textContent")
    if scheme:
        assert page.js("getComputedStyle(document.documentElement).colorScheme") == scheme
    page.js("location.reload()")
    page.wait(f"document.querySelector('input[value={design}]')?.checked")
    page.click("[data-home-link]")
    page.wait("location.pathname === '/' && !!document.getElementById('widget')")
    assert page.js("document.documentElement.dataset.design") == design
    if design != "woodland":
        assert "woodland-background" not in page.js("getComputedStyle(document.body).backgroundImage")
    if design in {"city", "mountain", "ocean", "japan"}:
        asset = f"/static/assets/{design}-background.webp"
        assert asset in page.js("getComputedStyle(document.body).backgroundImage")
        assert page.js(f"""new Promise(resolve => {{
            const img = new Image(); img.onload = () => resolve(img.naturalWidth > 1000);
            img.onerror = () => resolve(false); img.src = {json.dumps(asset)};
        }})""") is True
        assert page.js(f"getComputedStyle(document.querySelector('.theme-ornament-{design}')).display") == "block"
    if design == "basic":
        assert page.js("getComputedStyle(document.body).backgroundImage") == "none"
        assert page.js("getComputedStyle(document.querySelector('.ornament')).display") == "none"
    if scheme:
        assert page.js("getComputedStyle(document.documentElement).colorScheme") == scheme


def test_theme_inconnu_et_stockage_indisponible(page):
    page.js("localStorage.setItem('dt.design', 'inconnu'); location.reload()")
    page.wait("document.readyState === 'complete' && !!document.getElementById('settingsLink')")
    assert page.js("document.documentElement.dataset.design") == "basic"
    page.click("#settingsLink")
    page.wait("document.querySelectorAll('[name=design]').length === 6")
    page.js("Storage.prototype.setItem = () => { throw new Error('stockage bloqué'); }")
    page.click("input[value=city]")
    assert page.js("document.documentElement.dataset.design") == "city"
    assert "ne sera pas conservé" in page.js("document.querySelector('[data-theme-notice]').textContent")


def test_theme_synchronise_et_retour_avec_cle_acces(page):
    page.js("location.href = '/?key=test-access'")
    page.wait("document.getElementById('settingsLink')?.href.endsWith('?key=test-access')")
    page.click("#settingsLink")
    page.wait("document.querySelectorAll('[name=design]').length === 6")
    # Une autre fenêtre choisit un thème, puis efface les préférences.
    page.js("window.dispatchEvent(new StorageEvent('storage', {key: 'dt.design', newValue: 'ocean'}))")
    assert page.js("document.querySelector('input[value=ocean]').checked") is True
    page.js("window.dispatchEvent(new StorageEvent('storage', {key: null}))")
    assert page.js("document.querySelector('input[value=basic]').checked") is True
    page.click("[data-home-link]")
    page.wait("location.pathname === '/' && location.search === '?key=test-access'")


def test_themes_clairs_sur_systeme_sombre_et_petits_ecrans(page):
    page.command("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": "dark"}]})
    page.command("Emulation.setDeviceMetricsOverride", {
        "width": 360, "height": 800, "deviceScaleFactor": 1, "mobile": False})
    page.click("#settingsLink")
    page.wait("document.querySelectorAll('[name=design]').length === 6")
    for design, scheme in [("mountain", "light"), ("ocean", "light"), ("city", "dark"),
                          ("japan", "dark"), ("woodland", "dark"), ("basic", "dark")]:
        page.click(f"input[value={design}]")
        assert page.js("getComputedStyle(document.documentElement).colorScheme") == scheme
        assert page.js("document.documentElement.scrollWidth <= innerWidth") is True
    page.click("[data-home-link]")
    page.wait("location.pathname === '/' && !!document.getElementById('widget')")
    assert page.js("document.documentElement.scrollWidth <= innerWidth") is True


@pytest.mark.parametrize("old,new", [("paper", "basic"), ("midnight", "city"), ("aurora", "ocean")])
def test_anciens_themes_repris(page, old, new):
    page.js(f"localStorage.setItem('dt.design', {json.dumps(old)}); location.reload()")
    page.wait("document.readyState === 'complete' && !!document.getElementById('settingsLink')")
    assert page.js("document.documentElement.dataset.design") == new
    page.click("#settingsLink")
    page.wait(f"document.querySelector('input[value={new}]')?.checked")


def test_basique_suit_le_systeme_sans_decor(page):
    assert page.js("document.documentElement.dataset.design") == "basic"
    for scheme in ("light", "dark", "light"):
        page.command("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": scheme}]})
        assert page.js("getComputedStyle(document.documentElement).colorScheme") == scheme
        assert page.js("getComputedStyle(document.body).backgroundImage") == "none"


def test_barres_de_defilement_assorties_aux_six_themes(page):
    page.command("Emulation.setDeviceMetricsOverride", {
        "width": 600, "height": 600, "deviceScaleFactor": 1, "mobile": False})
    page.click("#settingsLink")
    page.wait("document.querySelectorAll('[name=design]').length === 6")
    colors = []
    for design in ("basic", "woodland", "city", "mountain", "ocean", "japan"):
        page.click(f"input[value={design}]")
        style = page.js("""(() => {
            const root = document.documentElement;
            const sample = document.createElement('span'); root.append(sample);
            sample.style.background = 'var(--scroll-thumb)';
            const thumb = getComputedStyle(sample).backgroundColor;
            sample.style.background = 'var(--scroll-track)';
            const track = getComputedStyle(sample).backgroundColor;
            sample.remove();
            return {thumb, track,
                actualThumb: getComputedStyle(root, '::-webkit-scrollbar-thumb').backgroundColor,
                actualTrack: getComputedStyle(root, '::-webkit-scrollbar-track').backgroundColor,
                radius: getComputedStyle(root, '::-webkit-scrollbar-thumb').borderRadius,
                buttons: getComputedStyle(root, '::-webkit-scrollbar-button').display};
        })()""")
        assert style["actualThumb"] == style["thumb"]
        assert style["actualTrack"] == style["track"]
        assert style["radius"] == "999px"
        assert style["buttons"] == "none"
        colors.append(style["thumb"])
    assert len(set(colors)) == 6
    page.js("window.scrollTo(0, document.body.scrollHeight)")
    page.wait("scrollY > 0")


def test_barres_de_defilement_des_panneaux_et_onglets(page):
    for selector in ("#transcript", "#historyList", "#summary", "#profileTabs"):
        assert page.js(f"getComputedStyle(document.querySelector('{selector}'), '::-webkit-scrollbar-thumb').borderRadius") == "999px"
        assert page.js(f"getComputedStyle(document.querySelector('{selector}')).scrollbarWidth") == "auto"
    session = discord_sessions.start("b1", "p1", "Studio", "Réu")
    session.segments.extend({"speaker": "Marc", "user_id": "u1", "start": index, "end": index + 1,
                            "text": "Une ligne de transcription."} for index in range(100))
    page.wait("document.getElementById('transcript').value.includes('Une ligne de transcription')")
    page.click("#tabTranscript")
    page.js("document.getElementById('transcript').scrollTop = document.getElementById('transcript').scrollHeight")
    assert page.js("document.getElementById('transcript').scrollTop") > 0


def test_logo_micro_meme_forme_et_couleurs_du_theme(page):
    shapes, colors, silhouettes = [], [], []
    for design in ("basic", "woodland", "city", "mountain", "ocean", "japan"):
        page.js(f"localStorage.setItem('dt.design', {json.dumps(design)}); location.reload()")
        page.wait(f"document.readyState === 'complete' && document.documentElement.dataset.design === '{design}' && !!document.querySelector('.logo')")
        icon = page.js("""(async () => {
            const href = document.querySelector('link[rel=icon]').href;
            const svg = new DOMParser().parseFromString(decodeURIComponent(href.split(',')[1]), 'image/svg+xml');
            const canvas = document.createElement('canvas'); canvas.width = canvas.height = 64;
            const image = new Image(); image.src = await AppIcon.png(href);
            await image.decode(); canvas.getContext('2d').drawImage(image, 0, 0);
            const pixels = canvas.getContext('2d').getImageData(0, 0, 64, 64).data;
            return {path: svg.querySelector('path').getAttribute('d'), radius: svg.querySelector('rect').getAttribute('rx'),
                stroke: svg.querySelector('path').getAttribute('stroke'), accent: svg.querySelector('stop[offset]').getAttribute('stop-color'),
                alpha: Array.from({length: 4096}, (_, i) => pixels[i * 4 + 3]).join(','),
                box: [getComputedStyle(document.querySelector('.logo')).width, getComputedStyle(document.querySelector('.logo')).height]};
        })()""")
        shapes.append((icon["path"], icon["radius"], icon["box"]))
        colors.append(icon["accent"])
        silhouettes.append(icon["alpha"])
        assert icon["stroke"] == page.js("getComputedStyle(document.documentElement).getPropertyValue('--accent-text').trim()")
        assert icon["accent"] == page.js("getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()")
    assert all(shape == shapes[0] for shape in shapes)
    assert len(set(colors)) == 6
    assert len(set(silhouettes)) == 1


def test_cadre_natif_synchronise_au_demarrage_et_aux_changements(page):
    page.command("Page.enable", {})
    page.command("Page.addScriptToEvaluateOnNewDocument", {"source": """
        window.nativePalettes = [];
        window.pywebview = {api: {set_window_theme: async palette => {
            window.nativePalettes.push(palette); return true;
        }}};
    """})
    page.click("#settingsLink")
    page.wait("document.querySelectorAll('[name=design]').length === 6 && window.nativePalettes?.length > 0")
    for design in ("city", "mountain", "ocean", "japan", "woodland", "basic"):
        page.click(f"input[value={design}]")
        page.wait("window.nativePalettes.at(-1).background === getComputedStyle(document.documentElement).getPropertyValue('--card').trim()")
        palette = page.js("window.nativePalettes.at(-1)")
        assert palette["icon"].startswith("data:image/png;base64,")
        assert palette["text"] == page.js("getComputedStyle(document.documentElement).getPropertyValue('--text').trim()")
        assert palette["dark"] == page.js("getComputedStyle(document.documentElement).colorScheme === 'dark'")
    for scheme in ("light", "dark"):
        page.command("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": scheme}]})
        page.wait(f"window.nativePalettes.at(-1).dark === {str(scheme == 'dark').lower()}")


def test_cadre_natif_attend_le_pont_et_supporte_une_erreur(page):
    page.js("window.nativeCalls = []; window.pywebview = {api: {set_window_theme: async p => { window.nativeCalls.push(p); throw new Error('cadre indisponible'); }}}")
    page.js("window.dispatchEvent(new Event('pywebviewready'))")
    page.wait("window.nativeCalls.length > 0")
    page.js("window.pywebview.api.set_window_theme = async p => window.nativeCalls.push(p)")
    was_dark = page.js("getComputedStyle(document.documentElement).colorScheme === 'dark'")
    page.command("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": "light" if was_dark else "dark"}]})
    page.wait(f"window.nativeCalls.length > 1 && window.nativeCalls.at(-1).dark === {str(not was_dark).lower()}")


def test_theme_independant_des_reglages_de_bureau(page):
    page.command("Page.enable", {})
    page.command("Page.addScriptToEvaluateOnNewDocument", {"source": """
        window.settingsCalls = [];
        window.pywebview = {api: {
            get_settings: async () => ({values: {OLLAMA_MODEL: 'mistral-nemo'}, bots: []}),
            save_settings: async () => { window.settingsCalls.push('save'); return {restart: false}; },
            restart: async () => window.settingsCalls.push('restart'),
        }};
    """})
    page.click("#settingsLink")
    page.wait("document.getElementById('form')?.hidden === false && document.querySelectorAll('[name=design]').length === 6")
    page.js("document.getElementById('OLLAMA_MODEL').value = 'autre-modele'")
    page.click("input[value=city]")
    assert page.js("document.getElementById('OLLAMA_MODEL').value") == "autre-modele"
    assert page.js("window.settingsCalls") == []
    page.click("#backBtn")
    page.wait("location.pathname === '/' && !!document.getElementById('widget')")
    assert page.js("document.documentElement.dataset.design") == "city"


def test_bandeau_de_preparation_du_resume_local(page, monkeypatch):
    from app import local_ai
    monkeypatch.setattr(summarizer, "OLLAMA_MODEL", "mistral-nemo")
    monkeypatch.setitem(local_ai._state, "phase", "pulling")
    monkeypatch.setitem(local_ai._state, "detail", "Téléchargement du modèle mistral-nemo…")
    monkeypatch.setitem(local_ai._state, "progress", 0.37)
    page.js("location.reload()")
    page.wait("document.getElementById('aiBanner')?.hidden === false")
    assert page.js("aiText.textContent").startswith("Préparation du résumé local — Téléchargement du modèle mistral-nemo… 37 %")
    assert page.js("aiProgress.style.width") == "37%"

    # Prêt : le bandeau disparaît tout seul
    local_ai._state.update(phase="ready", progress=None)
    page.wait("document.getElementById('aiBanner')?.hidden === true")

    # Échec : message d'erreur à la place de la progression
    local_ai._state.update(phase="error", error="espace disque insuffisant")
    page.js("location.reload()")
    page.wait("document.getElementById('aiBanner')?.hidden === false")
    assert page.js("aiText.textContent") == "Résumé local indisponible : espace disque insuffisant"
    assert page.js("aiBar.hidden") is True
