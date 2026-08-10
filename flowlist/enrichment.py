"""
Busca BPM (tempo) e tom (key/mode) de cada faixa, pra alimentar o
algoritmo de ordenação em ordering.py.

Fonte 1 (preferida): endpoint audio-features da própria Spotify.
    -> Desde nov/2024 a Spotify BLOQUEIA esse endpoint para apps novos
       (criados/aprovados depois da mudança). Se o seu app não tiver
       acesso, a chamada volta 403 e a gente simplesmente pula essa fonte
       - não tem workaround oficial, é decisão deles.
       https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api

Fonte 2 (opcional, --use-getsongbpm): API pública da getsongbpm.com,
    que EXISTE justamente pra esse tipo de uso (tem endpoint oficial,
    com API key própria, ao contrário de raspar HTML de sites de BPM,
    que a maioria bloqueia explicitamente esse tipo de acesso automatizado).
    Registre uma key gratuita em https://getsongbpm.com/api
    Os nomes exatos dos campos podem variar; ajuste `_parse_getsongbpm_song`
    se a resposta da sua conta vier em formato diferente (rode com
    --debug-bpm pra ver o JSON cru).
"""

from __future__ import annotations

import os
import time

import requests
from spotipy import SpotifyException

from .spotify_client import Track

GETSONGBPM_BASE = "https://api.getsong.co"

# key (pitch class 0-11) + mode (0=menor,1=maior) -> código Camelot
_PITCH_CLASSES = ["C", "C#/Db", "D", "D#/Eb", "E", "F", "F#/Gb", "G", "G#/Ab", "A", "A#/Bb", "B"]
_CAMELOT_MAJOR = {0: "8B", 1: "3B", 2: "10B", 3: "5B", 4: "12B", 5: "7B",
                  6: "2B", 7: "9B", 8: "4B", 9: "11B", 10: "6B", 11: "1B"}
_CAMELOT_MINOR = {0: "5A", 1: "12A", 2: "7A", 3: "2A", 4: "9A", 5: "4A",
                  6: "11A", 7: "6A", 8: "1A", 9: "8A", 10: "3A", 11: "10A"}


def key_mode_to_camelot(key: int, mode: int) -> str | None:
    if key is None or key < 0:
        return None
    return _CAMELOT_MAJOR[key] if mode == 1 else _CAMELOT_MINOR[key]


def enrich_with_spotify_audio_features(sp, tracks: list[Track]) -> bool:
    """Tenta preencher tempo/key/mode via audio-features. Retorna False
    (sem levantar exceção) se o endpoint estiver bloqueado pro seu app."""
    ids = [t.id for t in tracks]
    by_id = {t.id: t for t in tracks}
    try:
        for i in range(0, len(ids), 100):
            batch = sp.audio_features(ids[i : i + 100])
            for feat in batch:
                if not feat:
                    continue
                track = by_id[feat["id"]]
                track.tempo = feat["tempo"]
                track.key = feat["key"]
                track.mode = feat["mode"]
                track.source = "spotify"
        return True
    except SpotifyException as e:
        if e.http_status == 403:
            print(
                "⚠ audio-features bloqueado pra este app (restrição da Spotify "
                "desde nov/2024 pra apps novos). Pulando essa fonte."
            )
        else:
            print(f"⚠ Erro ao consultar audio-features da Spotify: {e}")
        return False


def _parse_key_string(raw: str) -> tuple[int | None, int | None]:
    """Converte algo como 'C#m', 'Ab', 'F Major' pra (pitch_class, mode)."""
    if not raw:
        return None, None
    s = raw.strip()
    is_minor = s.lower().endswith("m") and not s.lower().endswith("major")
    is_minor = is_minor or "minor" in s.lower()
    name = s.replace("Major", "").replace("major", "").replace("Minor", "").replace("minor", "")
    name = name.rstrip("mM ").strip()
    name = name.replace("♯", "#").replace("♭", "b")
    aliases = {
        "Db": "C#/Db", "C#": "C#/Db", "Eb": "D#/Eb", "D#": "D#/Eb",
        "Gb": "F#/Gb", "F#": "F#/Gb", "Ab": "G#/Ab", "G#": "G#/Ab",
        "Bb": "A#/Bb", "A#": "A#/Bb",
    }
    canonical = aliases.get(name, name)
    if canonical not in _PITCH_CLASSES:
        return None, None
    pitch_class = _PITCH_CLASSES.index(canonical)
    return pitch_class, 0 if is_minor else 1


def _parse_getsongbpm_song(song: dict) -> tuple[float | None, int | None, int | None]:
    """Melhor esforço: tenta os nomes de campo mais comuns na resposta da
    getsongbpm.com. Rode com --debug-bpm pra conferir/ajustar."""
    tempo = song.get("tempo")
    tempo = float(tempo) if tempo not in (None, "") else None

    key_raw = song.get("key_of") or song.get("key") or ""
    pitch_class, mode = _parse_key_string(str(key_raw))
    return tempo, pitch_class, mode


def enrich_with_getsongbpm(tracks: list[Track], debug: bool = False, delay_seconds: float = 1.0) -> int:
    """Preenche (sequencialmente, com rate-limit educado) as faixas que
    ainda não têm tempo/key. Retorna quantas faixas foram enriquecidas."""
    api_key = os.environ.get("GETSONGBPM_API_KEY")
    if not api_key:
        print("⚠ --use-getsongbpm pedido, mas GETSONGBPM_API_KEY não está no .env. Pulando.")
        return 0

    filled = 0
    pending = [t for t in tracks if t.tempo is None]
    for track in pending:
        lookup = f"song:{track.name} artist:{track.artists.split(',')[0]}"
        try:
            resp = requests.get(
                f"{GETSONGBPM_BASE}/search/",
                params={"api_key": api_key, "type": "both", "lookup": lookup},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if debug:
                print(f"[debug-bpm] {track.name}: {data}")
            results = data.get("search") or []
            if not results or not isinstance(results, list):
                continue
            tempo, key, mode = _parse_getsongbpm_song(results[0])
            if tempo:
                track.tempo = tempo
                track.key = key
                track.mode = mode
                track.source = "getsongbpm"
                filled += 1
        except requests.RequestException as e:
            print(f"⚠ getsongbpm falhou para '{track.name}': {e}")
        time.sleep(delay_seconds)  # respeita rate limit da API deles

    return filled
