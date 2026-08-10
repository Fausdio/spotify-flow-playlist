"""
Camada de acesso à API oficial do Spotify.

Tudo aqui usa endpoints documentados (spotipy). Nada de automação de
navegador — é mais robusto e não depende da UI do Spotify não mudar.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import spotipy
from spotipy.oauth2 import SpotifyOAuth

SCOPES = "playlist-modify-public playlist-modify-private playlist-read-private"

# Sufixos comuns de versões alternativas, usados só para deduplicar
# (ex.: não colocar "Stressed Out" e "Stressed Out - Live" duas vezes).
_VARIANT_SUFFIXES = re.compile(
    r"\s*[-–(\[]\s*(live|ao vivo|acoustic|acústic\w+|remix|remaster\w*|"
    r"radio edit|edit|mono|stereo|deluxe|bonus track|"
    r"mtv unplugged|karaoke|instrumental|sped up|slowed).*$",
    re.IGNORECASE,
)


def normalize_title(title: str) -> str:
    """Reduz o nome da faixa à 'versão base', pra deduplicar regravações."""
    base = _VARIANT_SUFFIXES.sub("", title)
    base = re.sub(r"[^\w\s]", "", base).strip().lower()
    return base


def get_spotify_client() -> spotipy.Spotify:
    """Autentica via OAuth (Authorization Code Flow) usando credenciais do .env.

    Na primeira execução abre o navegador para você aprovar o app na sua
    própria conta Spotify. O token fica cacheado localmente (.cache-flowlist).
    """
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    if not client_id or not client_secret:
        raise SystemExit(
            "Faltam SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET no .env.\n"
            "Veja o README.md -> 'Configuração' para criar seu app gratuito."
        )

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_path=".cache-flowlist",
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth_manager)


@dataclass
class Track:
    id: str
    uri: str
    name: str
    artists: str
    popularity: int
    duration_ms: int
    album: str
    # Preenchido depois pelo módulo enrichment (pode ficar None)
    tempo: float | None = None
    key: int | None = None
    mode: int | None = None
    key_str: str | None = None  # usado quando vem de fonte que não é a Spotify
    source: str = field(default="none")  # "spotify" | "getsongbpm" | "none"


def _track_from_full_object(t: dict) -> Track:
    return Track(
        id=t["id"],
        uri=t["uri"],
        name=t["name"],
        artists=", ".join(a["name"] for a in t["artists"]),
        popularity=t.get("popularity", 0),
        duration_ms=t.get("duration_ms", 0),
        album=t.get("album", {}).get("name", ""),
    )


def find_artist_id(sp: spotipy.Spotify, artist_name: str) -> tuple[str, str]:
    result = sp.search(q=f"artist:{artist_name}", type="artist", limit=5)
    items = result["artists"]["items"]
    if not items:
        raise SystemExit(f"Nenhum artista encontrado para '{artist_name}'.")
    # heurística simples: pega o de maior "followers" entre os primeiros resultados
    best = max(items, key=lambda a: a.get("followers", {}).get("total", 0))
    return best["id"], best["name"]


def get_artist_best_tracks(sp: spotipy.Spotify, artist_name: str, top_n: int) -> list[Track]:
    """Varre toda a discografia (álbuns + singles), remove versões duplicadas
    e devolve as `top_n` faixas de maior popularidade — cobrindo a carreira
    inteira, não só o Top 10 (que é o limite do endpoint artist-top-tracks).
    """
    artist_id, artist_name_resolved = find_artist_id(sp, artist_name)

    album_ids: list[str] = []
    seen_album_names: set[str] = set()
    results = sp.artist_albums(artist_id, album_type="album,single", country="US", limit=50)
    while results:
        for album in results["items"]:
            key = normalize_title(album["name"])
            if key in seen_album_names:
                continue
            seen_album_names.add(key)
            album_ids.append(album["id"])
        results = sp.next(results) if results.get("next") else None

    track_ids: list[str] = []
    for i in range(0, len(album_ids), 20):  # album_tracks não é batch; sp.albums sim
        for album in sp.albums(album_ids[i : i + 20])["albums"]:
            for t in album["tracks"]["items"]:
                track_ids.append(t["id"])

    full_tracks: list[dict] = []
    for i in range(0, len(track_ids), 50):
        batch = sp.tracks(track_ids[i : i + 50])["tracks"]
        full_tracks.extend(t for t in batch if t)

    # deduplica por título normalizado, mantendo a versão mais popular
    best_by_title: dict[str, dict] = {}
    for t in full_tracks:
        key = normalize_title(t["name"])
        if key not in best_by_title or t["popularity"] > best_by_title[key]["popularity"]:
            best_by_title[key] = t

    ranked = sorted(best_by_title.values(), key=lambda t: t["popularity"], reverse=True)
    chosen = ranked[:top_n]
    print(f"Artista: {artist_name_resolved} — {len(best_by_title)} faixas únicas encontradas, "
          f"selecionadas as {len(chosen)} mais populares.")
    return [_track_from_full_object(t) for t in chosen]


def extract_playlist_id(playlist_url_or_id: str) -> str:
    match = re.search(r"playlist/([A-Za-z0-9]+)", playlist_url_or_id)
    return match.group(1) if match else playlist_url_or_id


def get_playlist_tracks(sp: spotipy.Spotify, playlist_url_or_id: str) -> tuple[list[Track], str]:
    playlist_id = extract_playlist_id(playlist_url_or_id)
    playlist = sp.playlist(playlist_id, fields="name,tracks.items(track),tracks.next")
    name = playlist["name"]

    tracks: list[Track] = []
    results = playlist["tracks"]
    while results:
        for item in results["items"]:
            t = item.get("track")
            if t and t.get("id"):
                tracks.append(_track_from_full_object(t))
        results = sp.next(results) if results.get("next") else None

    print(f"Playlist original: '{name}' — {len(tracks)} faixas carregadas.")
    return tracks, name


def create_playlist(
    sp: spotipy.Spotify, name: str, description: str, uris: list[str], public: bool = False
) -> str:
    user_id = sp.current_user()["id"]
    playlist = sp.user_playlist_create(user_id, name, public=public, description=description)
    for i in range(0, len(uris), 100):  # add_items aceita no máx. 100 por chamada
        sp.playlist_add_items(playlist["id"], uris[i : i + 100])
    return playlist["external_urls"]["spotify"]
