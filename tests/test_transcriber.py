"""Transcription : ré-échantillonnage, et vraie transcription Whisper d'une phrase enregistrée."""

import wave

import numpy as np
import pytest

from app import transcriber
from tests.conftest import FIXTURES


def test_reechantillonnage_48k_vers_16k():
    audio = np.arange(48000, dtype=np.float32)
    out = transcriber.resample(audio, 48000)
    assert out.dtype == np.float32 and len(out) == 16000
    assert out[0] == pytest.approx(1.0)  # moyenne de 0, 1, 2


def test_reechantillonnage_frequence_quelconque():
    out = transcriber.resample(np.ones(22050, dtype=np.float32), 22050)
    assert len(out) == 16000 and np.allclose(out, 1.0)


def test_audio_vide():
    assert transcriber.transcribe_audio(np.zeros(0, dtype=np.float32), 48000) == []


@pytest.mark.slow
def test_transcription_reelle_d_une_phrase_en_francais():
    with wave.open(str(FIXTURES / "bonjour.wav")) as w:
        rate = w.getframerate()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    segments = transcriber.transcribe_audio(audio, rate, "Réunion entre Marc et Julie.")
    text = " ".join(s["text"] for s in segments).lower()
    for word in ("bonjour", "budget", "julie", "présentation", "jeudi"):
        assert word in text, text
