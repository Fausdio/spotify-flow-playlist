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
from .text_utils import title_variants as _title_variants

GETSONGBPM_BASE = "https://api.getsong.co"

# getsongbpm.com é uma base colaborativa: geralmente só tem a versão
# "canônica" de uma música catalogada, não remixes/ao vivo/features/sessions
# específicas. Pra essas, o título exato (como vem da Spotify) nunca bate,
# mesmo a música "base" estando lá. Isso NÃO toca a API da Spotify — é tudo
# contra a getsongbpm, então dá pra tentar à vontade sem risco de rate
# limit, só o tempo de espera entre chamadas por educação com o serviço.
# (a lógica de limpeza de título mora em text_utils.py, compartilhada com
# o dedup de spotify_client.normalize_title)

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


def _lookup_getsongbpm(api_key: str, title: str, artist: str, debug: bool, track_name: str) -> dict | None:
    resp = requests.get(
        f"{GETSONGBPM_BASE}/search/",
        params={"api_key": api_key, "type": "both", "lookup": f"song:{title} artist:{artist}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if debug:
        print(f"[debug-bpm] {track_name} (tentativa '{title}'): {data}")
    results = data.get("search")
    if not results or not isinstance(results, list):
        return None
    return results[0]


NOT_FOUND_MARKER = "getsongbpm_not_found"


def enrich_with_getsongbpm(tracks: list[Track], debug: bool = False, delay_seconds: float = 1.0) -> int:
    """Preenche (sequencialmente, com pausa educada) as faixas que ainda não
    têm tempo/key. Pra cada uma, tenta o título exato primeiro e, se não
    achar, tenta versões mais "limpas" do título (sem "- Live", "(feat. X)",
    "- Session", etc.) — a getsongbpm.com geralmente só tem a versão base
    catalogada.

    Faixas que esgotam todas as tentativas sem sucesso ficam marcadas
    (`source = "getsongbpm_not_found"`) — isso vai pro cache local, então
    reruns seguintes não tentam de novo essas faixas conhecidas (só as
    novas). Erro de rede não conta como "não encontrada" (pode ser só um
    problema passageiro); só uma resposta vazia de verdade marca.

    Retorna quantas faixas foram enriquecidas nesta chamada."""
    api_key = os.environ.get("GETSONGBPM_API_KEY")
    if not api_key:
        print("⚠ --use-getsongbpm pedido, mas GETSONGBPM_API_KEY não está no .env. Pulando.")
        return 0

    filled = 0
    fallback_hits = 0
    already_known = sum(1 for t in tracks if t.tempo is None and t.source == NOT_FOUND_MARKER)
    pending = [t for t in tracks if t.tempo is None and t.source != NOT_FOUND_MARKER]
    if already_known:
        print(
            f"   ({already_known} faixa(s) já tinham sido buscadas antes e não foram "
            "encontradas — pulando de novo. Use --refresh-cache pra tentar tudo de novo.)"
        )

    for track in pending:
        artist = track.artists.split(",")[0].strip()
        found = False
        network_error = False
        for attempt, title in enumerate(_title_variants(track.name)):
            try:
                song = _lookup_getsongbpm(api_key, title, artist, debug, track.name)
            except requests.RequestException as e:
                print(f"⚠ getsongbpm falhou para '{track.name}': {e}")
                network_error = True
                break  # erro de rede — não adianta insistir agora, vai pra próxima faixa
            finally:
                time.sleep(delay_seconds)  # respeita o serviço, mesmo em erro/sem resultado

            if song is None:
                continue
            tempo, key, mode = _parse_getsongbpm_song(song)
            if tempo:
                track.tempo = tempo
                track.key = key
                track.mode = mode
                track.source = "getsongbpm"
                filled += 1
                found = True
                if attempt > 0:
                    fallback_hits += 1
                break

        if not found and not network_error:
            track.source = NOT_FOUND_MARKER

    if fallback_hits:
        print(f"   (dessas, {fallback_hits} só foram encontradas tentando um título simplificado)")
    return filled
