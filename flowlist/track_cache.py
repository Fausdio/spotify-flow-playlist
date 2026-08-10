"""
Cache local das faixas já buscadas na Spotify, por artista.

Existe só por causa da realidade descoberta na prática: com os endpoints em
lote bloqueados, buscar a discografia de um artista custa uma chamada por
faixa (100+ pra um artista médio). Sem cache, cada teste reroda esse custo
inteiro contra um rate limit que já se mostrou bem apertado. Com cache, só a
primeira busca de cada artista paga esse preço — reordenar, trocar fonte de
BPM ou testar de novo usa o que já foi salvo.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from .spotify_client import Track

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG_RE.sub("-", name.lower()).strip("-") or "artista"


def cache_path(artist_name: str) -> Path:
    return Path(f".cache-tracks-{_slug(artist_name)}.json")


def load(key: str) -> tuple[list[Track], str | None] | None:
    """Retorna (tracks, source_name) do cache, ou None se não existir/inválido."""
    path = cache_path(key)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        tracks = [Track(**item) for item in raw["tracks"]]
        return tracks, raw.get("source_name")
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        print(f"⚠ Cache de '{key}' corrompido ou desatualizado ({e}); ignorando.")
        return None


def save(key: str, tracks: list[Track], source_name: str | None = None) -> None:
    path = cache_path(key)
    path.write_text(
        json.dumps(
            {"source_name": source_name, "tracks": [asdict(t) for t in tracks]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
