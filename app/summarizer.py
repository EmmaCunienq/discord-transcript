"""Génération du compte rendu de réunion, avec Ollama (local) ou Claude."""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator

import anthropic

from . import local_ai

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")

# Si OLLAMA_MODEL est défini, le résumé est rédigé en local par Ollama plutôt que par Claude
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "32768"))
OLLAMA_MAX_OUTPUT = 4096

SYSTEM_PROMPT = """Tu rédiges des comptes rendus de réunion en français à partir de transcriptions automatiques.

La transcription vient d'un outil de reconnaissance vocale : elle peut contenir des mots mal reconnus \
et des phrases coupées. Quand le sens d'un passage est incertain, reste prudent plutôt que d'interpréter, \
et ne rapporte que ce qui a réellement été dit — un compte rendu qui invente une décision ou une échéance \
est pire qu'un compte rendu incomplet.

Intervenants : si les lignes sont de la forme `[mm:ss] Nom : texte`, chaque personne a été enregistrée \
sur sa propre piste, donc l'attribution est fiable — sers-t'en pour indiquer qui propose, décide ou prend \
une action. Les horodatages permettent de voir que des personnes ont parlé en même temps (réaction, \
interruption) : lis ces lignes ensemble. Sans ces préfixes, la transcription ne distingue pas les \
intervenants : n'attribue alors une action à quelqu'un que si son nom est prononcé.

Format attendu (Markdown) :

## En bref
2 à 4 phrases : l'objet de la réunion et ce qui en ressort.

## Points abordés
Les sujets discutés, regroupés par thème, avec les arguments ou chiffres importants.

## Décisions
Ce qui a été tranché. Si rien n'a été décidé, écris « Aucune décision explicite ».

## Actions à mener
Une ligne par action, sans guillemets ni accents graves, exactement ainsi :
- [ ] **Responsable** — action (échéance)
Mets « Non précisé » pour \
un responsable ou une échéance qui n'a pas été mentionné.

## Questions ouvertes
Les points restés en suspens ou à approfondir. Omets la section s'il n'y en a pas.

Adapte la longueur à la durée et à la densité de la réunion : quelques lignes pour un point rapide, \
plus de détail pour une longue réunion de travail. Écris directement le compte rendu, sans phrase d'introduction."""


class SummaryError(Exception):
    pass


def summarize(transcript: str, context: str = "") -> str:
    """Résumé complet (utilisé en fin de réunion Discord) ; lève SummaryError en cas d'échec."""
    if OLLAMA_MODEL:
        local_ai.wait_ready()  # fin de réunion pendant le premier téléchargement : on attend plutôt qu'échouer
    return "".join(generate(transcript, context)).strip()


def _generate_ollama(user_content: str) -> Iterator[str]:
    state = local_ai.status()
    if state["phase"] == "error":
        raise SummaryError(f"Ollama indisponible : {state['error']}")
    if state["phase"] not in ("ready", "off"):  # off : Ollama non géré par l'app (lancé à part)
        progress = f" ({state['progress']:.0%})" if state["progress"] is not None else ""
        raise SummaryError(f"le résumé local se prépare — {state['detail']}{progress} Réessaie dans un moment.")
    # Ollama tronque sans prévenir ce qui dépasse num_ctx : mieux vaut refuser qu'un résumé
    # qui ignore une partie de la réunion. Estimation prudente : 3 caractères par token.
    if (len(SYSTEM_PROMPT) + len(user_content)) / 3 > OLLAMA_NUM_CTX - OLLAMA_MAX_OUTPUT:
        raise SummaryError("transcription trop longue pour le modèle local (augmente `OLLAMA_NUM_CTX`).")

    req = urllib.request.Request(
        f"{local_ai.OLLAMA_URL}/api/chat",
        data=json.dumps({
            "model": OLLAMA_MODEL,
            "stream": True,
            "options": {"num_ctx": OLLAMA_NUM_CTX, "num_predict": OLLAMA_MAX_OUTPUT, "temperature": 0.2},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as res:
            for line in res:
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise SummaryError(f"erreur Ollama : {chunk['error']}")
                yield chunk.get("message", {}).get("content", "")
                if chunk.get("done") and chunk.get("done_reason") == "length":
                    yield "\n\n> ⚠️ Résumé tronqué (longueur maximale atteinte)."
    except urllib.error.HTTPError as e:
        raise SummaryError(f"erreur Ollama ({e.code}) : modèle `{OLLAMA_MODEL}` installé ? ({e.read().decode(errors='replace')})") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise SummaryError("impossible de joindre Ollama (l'application est-elle lancée ?).") from e


def generate(transcript: str, context: str) -> Iterator[str]:
    """Compte rendu en flux (morceaux de texte) ; lève SummaryError en cas d'échec."""
    user_content = ""
    if context.strip():
        user_content += f"<contexte>\n{context.strip()}\n</contexte>\n\n"
    user_content += f"<transcription>\n{transcript.strip()}\n</transcription>"

    if OLLAMA_MODEL:
        yield from _generate_ollama(user_content)
        return

    client = anthropic.Anthropic()
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            # Si les filtres de sécurité refusent la requête, l'API la relance
            # automatiquement sur le modèle de repli recommandé.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise SummaryError("identifiants Anthropic invalides (vérifie `ANTHROPIC_API_KEY`).") from e
    except TypeError as e:
        # Le SDK lève TypeError quand aucun identifiant n'est configuré
        if "authentication" not in str(e):
            raise
        raise SummaryError("aucune clé Anthropic configurée (définis `ANTHROPIC_API_KEY` puis relance le serveur).") from e
    except anthropic.RateLimitError as e:
        raise SummaryError("limite de requêtes atteinte, réessaie dans un instant.") from e
    except anthropic.APIStatusError as e:
        raise SummaryError(f"erreur API ({e.status_code}) : {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise SummaryError("impossible de joindre l'API Anthropic (connexion réseau ?).") from e

    if final.stop_reason == "refusal":
        raise SummaryError("le résumé n'a pas pu être généré pour cette transcription.")
    if final.stop_reason == "max_tokens":
        yield "\n\n> ⚠️ Résumé tronqué (longueur maximale atteinte)."
