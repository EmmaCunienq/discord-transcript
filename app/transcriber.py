"""Transcription locale avec faster-whisper (l'audio ne quitte pas la machine)."""

import os
import threading

import numpy as np
from faster_whisper import WhisperModel

TARGET_SR = 16000

MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")
LANGUAGE = os.getenv("WHISPER_LANGUAGE", "fr") or None  # vide = détection auto

_model: WhisperModel | None = None
_lock = threading.Lock()


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        # int8 sur CPU : bon compromis vitesse/qualité, sans carte graphique
        _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def resample(audio: np.ndarray, sr: int) -> np.ndarray:
    if sr == TARGET_SR:
        return audio
    if sr % TARGET_SR == 0:
        # 48 kHz -> 16 kHz : moyenne par blocs (fait office de filtre passe-bas simple)
        factor = sr // TARGET_SR
        usable = len(audio) - len(audio) % factor
        return audio[:usable].reshape(-1, factor).mean(axis=1).astype(np.float32)
    n = int(len(audio) * TARGET_SR / sr)
    x_old = np.arange(len(audio))
    x_new = np.linspace(0, len(audio) - 1, num=n)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def _run(audio: np.ndarray, prompt: str | None) -> list[dict]:
    with _lock:
        segments, _info = get_model().transcribe(
            audio,
            language=LANGUAGE,
            vad_filter=True,
            beam_size=5,
            initial_prompt=prompt or None,
        )
        return [
            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            for s in segments
            if s.text.strip()
        ]


def transcribe_audio(audio: np.ndarray, sample_rate: int, prompt: str | None = None) -> list[dict]:
    """Transcrit un signal mono float32 (valeurs dans [-1, 1]) à n'importe quelle fréquence."""
    if audio.size == 0:
        return []
    return _run(resample(audio, sample_rate), prompt)
