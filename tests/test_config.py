"""Bots et profils (config.json)."""

from app import config
from app.paths import CONFIG_FILE


def test_sans_fichier_aucun_bot_ni_profil():
    assert config.bots() == []
    assert config.profiles() == []


def test_migration_reprend_les_anciens_reglages(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "tok-123")
    monkeypatch.setenv("DISCORD_GUILD_ID", "g1")
    monkeypatch.setenv("NOTION_PAGE_ID", "page-1")
    config._migrate()

    [bot] = config.bots()
    assert bot["name"] == "Bot principal" and bot["token"] == "tok-123"
    [profile] = config.profiles()
    assert profile["name"] == "Réunions"
    assert profile["bot_id"] == bot["id"]
    assert profile["guild_id"] == "g1" and profile["notion_page"] == "page-1"
    assert profile["channel_id"] == ""


def test_migration_ne_remplace_pas_une_config_existante(monkeypatch, write_config):
    write_config(bots=[{"id": "b1", "name": "Existant", "token": "x"}])
    monkeypatch.setenv("DISCORD_TOKEN", "autre")
    config._migrate()
    assert [b["name"] for b in config.bots()] == ["Existant"]


def test_migration_sans_token_ne_cree_rien(monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    config._migrate()
    assert not CONFIG_FILE.exists()


def test_set_bots_ignore_les_lignes_sans_token_et_signale_les_changements():
    changed = config.set_bots([
        {"id": "", "name": " Principal ", "token": " t1 "},
        {"id": "", "name": "Vide", "token": "   "},
    ])
    assert changed
    [bot] = config.bots()
    assert bot["name"] == "Principal" and bot["token"] == "t1" and bot["id"]
    # Même liste (avec l'id attribué) : rien à redémarrer
    assert config.set_bots([{"id": bot["id"], "name": "Principal", "token": "t1"}]) is False


def test_set_bots_nom_par_defaut():
    config.set_bots([{"name": "  ", "token": "t"}])
    assert config.bots()[0]["name"] == "Bot"


def test_creation_profil_utilise_le_premier_bot(write_config):
    write_config(bots=[{"id": "b1", "name": "A", "token": "x"}, {"id": "b2", "name": "B", "token": "y"}])
    profile = config.create_profile("Atelier")
    assert profile["bot_id"] == "b1"
    assert set(profile) == {"id", *config.PROFILE_FIELDS}
    assert config.profile(profile["id"]) == profile


def test_mise_a_jour_profil_ignore_les_champs_inconnus(write_config):
    write_config(profiles=[{"id": "p1", "name": "Équipe"}])
    updated = config.update_profile("p1", {"name": "Équipe produit", "channel_id": "c1", "pirate": "x", "context": None})
    assert updated["name"] == "Équipe produit" and updated["channel_id"] == "c1"
    assert "pirate" not in updated
    assert config.update_profile("inconnu", {"name": "x"}) is None


def test_suppression_profil(write_config):
    write_config(profiles=[{"id": "p1"}, {"id": "p2"}])
    config.delete_profile("p1")
    assert [p["id"] for p in config.profiles()] == ["p2"]


def test_profil_d_un_enregistrement_lance_depuis_discord(write_config):
    write_config(profiles=[
        {"id": "autre-bot", "bot_id": "b2", "guild_id": "g1", "channel_id": "c1"},
        {"id": "meme-serveur", "bot_id": "b1", "guild_id": "g1", "channel_id": "c2"},
        {"id": "meme-salon", "bot_id": "b1", "guild_id": "g1", "channel_id": "c1"},
        {"id": "meme-bot", "bot_id": "b1", "guild_id": "g9", "channel_id": "c9"},
    ])
    assert config.match_profile("b1", "g1", "c1") == "meme-salon"
    assert config.match_profile("b1", "g1", "c7") == "meme-serveur"
    assert config.match_profile("b1", "g9", "c5") == "meme-bot"
    # Ni le salon ni le serveur : le premier profil du même bot
    assert config.match_profile("b1", "g5", "c5") == "meme-serveur"
    assert config.match_profile("b3", "g1", "c1") == ""


def test_config_illisible_reprend_la_copie_de_secours(write_config, capsys):
    write_config(bots=[{"id": "b1", "name": "Principal", "token": "t"}], profiles=[{"id": "p1", "name": "Atelier"}])
    config.create_profile("Second")
    CONFIG_FILE.write_text('{"bots": [', encoding="utf-8")  # fichier tronqué (modifié à la main, disque plein…)

    assert [p["name"] for p in config.profiles()] == ["Atelier", "Second"]  # rien de perdu
    assert [b["name"] for b in config.bots()] == ["Principal"]
    assert config.BROKEN_FILE.read_text(encoding="utf-8") == '{"bots": ['  # gardé pour l'utilisateur
    assert "config.json illisible" in capsys.readouterr().out


def test_config_illisible_sans_sauvegarde_repart_a_vide():
    CONFIG_FILE.write_bytes(b"\xff\xfe pas du json")
    assert config.bots() == [] and config.profiles() == []
    assert config.create_profile("Nouveau")["name"] == "Nouveau"  # l'app reste utilisable
    assert config.BROKEN_FILE.exists()


def test_config_incomplete_completee():
    CONFIG_FILE.write_text('{"profiles": [{"id": "p1", "name": "Ancien"}, "intrus", {"name": "sans id"}],'
                           ' "bots": [{"id": "b1", "token": "t"}, {"id": "b2"}]}', encoding="utf-8")
    [profile] = config.profiles()
    assert profile["id"] == "p1" and profile["name"] == "Ancien" and profile["channel_id"] == ""
    assert config.bots() == [{"id": "b1", "token": "t", "name": "Bot"}]
    assert not config.BROKEN_FILE.exists()
