"""Rédaction du compte rendu (Ollama simulé)."""

import io
import json
import urllib.error

import pytest

from app import summarizer


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def ollama_stream(*chunks, done_reason="stop"):
    lines = [json.dumps({"message": {"content": c}}) for c in chunks]
    lines.append(json.dumps({"done": True, "done_reason": done_reason}))
    return FakeResponse(("\n".join(lines) + "\n").encode())


@pytest.fixture
def ollama(monkeypatch):
    monkeypatch.setattr(summarizer, "OLLAMA_MODEL", "mistral-nemo")
    sent = []

    def use(response):
        def fake_urlopen(req, timeout):
            sent.append(json.loads(req.data))
            if isinstance(response, Exception):
                raise response
            return response
        monkeypatch.setattr(summarizer.urllib.request, "urlopen", fake_urlopen)
        return sent
    return use


def test_ollama_en_flux_avec_contexte(ollama):
    sent = ollama(ollama_stream("## En bref\n", "Budget validé."))
    out = summarizer.summarize("[00:01] Marc : On valide.", "Équipe produit")
    assert out == "## En bref\nBudget validé."
    [body] = sent
    assert body["model"] == "mistral-nemo" and body["stream"] is True
    user = body["messages"][1]["content"]
    assert "<contexte>\nÉquipe produit\n</contexte>" in user and "<transcription>" in user


def test_ollama_reponse_tronquee_signalee(ollama):
    ollama(ollama_stream("Début", done_reason="length"))
    assert "Résumé tronqué" in summarizer.summarize("texte")


def test_ollama_injoignable(ollama):
    ollama(urllib.error.URLError("refused"))
    with pytest.raises(summarizer.SummaryError, match="impossible de joindre Ollama"):
        summarizer.summarize("texte")


def test_ollama_erreur_dans_le_flux(ollama):
    ollama(FakeResponse(json.dumps({"error": "model not found"}).encode()))
    with pytest.raises(summarizer.SummaryError, match="model not found"):
        summarizer.summarize("texte")


def test_transcription_trop_longue_pour_le_modele_local(ollama, monkeypatch):
    sent = ollama(ollama_stream("x"))
    monkeypatch.setattr(summarizer, "OLLAMA_NUM_CTX", 6000)
    with pytest.raises(summarizer.SummaryError, match="trop longue"):
        summarizer.summarize("mot " * 20000)
    assert sent == []  # rien envoyé plutôt qu'un résumé qui ignorerait la fin de la réunion
