"""Publication Notion (l'API Notion est simulée : aucune page réelle n'est créée)."""

import pytest

from app import notion

PAGE = "4121963b2061801fbf20df517c086c78"
PAGE_UUID = "4121963b-2061-801f-bf20-df517c086c78"


@pytest.mark.parametrize("ref", [
    PAGE,
    PAGE_UUID,
    f"https://www.notion.so/Reunions-{PAGE}",
    f"https://www.notion.so/team/Reunions-{PAGE}?pvs=4#abc",
])
def test_page_id_accepte_lien_ou_id(ref):
    assert notion.page_id(ref) == PAGE_UUID


def test_page_id_invalide():
    with pytest.raises(notion.NotionError):
        notion.page_id("https://www.notion.so/pas-un-id")


def test_titre_de_reunion_en_francais():
    import time
    ts = time.mktime((2026, 10, 15, 14, 0, 0, 0, 0, -1))
    assert notion.meeting_title(ts) == "Réunion du 15 octobre 2026"


def test_transcription_en_blocs_avec_intervenants():
    [line, other] = notion.transcript_blocks("[00:01] Marc : On valide.\n\nTexte libre **important**")
    texts = line["paragraph"]["rich_text"]
    assert [t["text"]["content"] for t in texts] == ["[00:01] ", "Marc", " : On valide."]
    assert texts[1]["annotations"] == {"bold": True}
    assert other["paragraph"]["rich_text"][1]["annotations"] == {"bold": True}


def test_transcription_vide():
    [block] = notion.transcript_blocks("   ")
    assert block["paragraph"]["rich_text"][0]["text"]["content"] == "(transcription vide)"


def test_resume_en_blocs():
    blocks = notion.summary_blocks(
        "## En bref\nTexte.\n---\n### Détail\n- [ ] **Marc** — budget (jeudi)\n- [x] fait\n- point\n1. un\n> citation"
    )
    assert [b["type"] for b in blocks] == [
        "heading_3", "paragraph", "paragraph", "to_do", "to_do", "bulleted_list_item", "numbered_list_item", "quote",
    ]
    assert blocks[3]["to_do"]["checked"] is False and blocks[4]["to_do"]["checked"] is True


def test_texte_long_decoupe_en_morceaux_de_2000_caracteres():
    parts = notion._text("x" * 4500)
    assert [len(p["text"]["content"]) for p in parts] == [2000, 2000, 500]


def test_recherche_pages_filtre_les_comptes_rendus_et_la_corbeille(monkeypatch):
    def page(pid, title, emoji="", **extra):
        return {"id": pid, "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}},
                "icon": {"type": "emoji", "emoji": emoji} if emoji else None, **extra}

    responses = iter([
        {"results": [page("1", "Réunions"), page("2", "Réunion du 8 novembre 2026", notion.MEETING_EMOJI)],
         "has_more": True, "next_cursor": "c2"},
        {"results": [page("3", "Archives", archived=True), page("4", "Atelier", "🛠️"),
                     page("5", "Réunion du 9 novembre 2026")],  # même titre mais sans l'icône : page de l'utilisateur
         "has_more": False},
    ])
    calls = []

    def fake_request(method, path, body=None):
        calls.append(body.get("start_cursor"))
        return next(responses)

    monkeypatch.setattr(notion, "_request", fake_request)
    pages = notion.search_pages()
    assert [p["title"] for p in pages] == ["Atelier", "Réunion du 9 novembre 2026", "Réunions"]
    assert pages[0]["emoji"] == "🛠️"
    assert calls == [None, "c2"]


def test_publication_cree_la_page_puis_complete_les_longs_contenus(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
    requests = []

    def fake_request(method, path, body=None):
        requests.append((method, path, body))
        if path == "/pages":
            return {"id": "new-page", "url": "https://notion.so/new"}
        if path.startswith("/blocks/new-page/children?"):
            return {"results": [{"id": "toggle"}]}
        return {"results": body["children"]}

    monkeypatch.setattr(notion, "_request", fake_request)
    transcript = "\n".join(f"[00:{i:02d}] Marc : phrase {i}" for i in range(150))
    url = notion.publish(PAGE, transcript, "## En bref\nOk.", "Réunion du 1 janvier 2027")

    assert url == "https://notion.so/new"
    method, path, body = requests[0]
    assert (method, path) == ("POST", "/pages")
    assert body["parent"] == {"page_id": PAGE_UUID}
    assert body["icon"]["emoji"] == notion.MEETING_EMOJI
    assert len(body["children"][0]["heading_3"]["children"]) == notion.BATCH
    # Les 50 lignes restantes sont ajoutées dans le titre déroulant « Transcription »
    assert requests[-1][1] == "/blocks/toggle/children"
    assert len(requests[-1][2]["children"]) == 50


def test_publication_sans_jeton(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with pytest.raises(notion.NotionError):
        notion.publish(PAGE, "texte", "", "titre")
