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
from spotipy import SpotifyException
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


def get_spotify_client(account: str | None = None) -> spotipy.Spotify:
    """Autentica via OAuth (Authorization Code Flow) usando credenciais do .env.

    Na primeira execução (por conta) abre o navegador pra você aprovar o app.
    O token fica cacheado localmente em `.cache-flowlist` — ou
    `.cache-flowlist-<account>` se você passar `--account nome` no CLI, pra
    testar com mais de uma conta Spotify sem elas se sobrescreverem.

    Lembrete: enquanto o app estiver em "Development Mode" no dashboard da
    Spotify, cada conta usada aqui precisa estar cadastrada em
    Settings -> Users and Access, senão a autorização falha.
    """
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    if not client_id or not client_secret:
        raise SystemExit(
            "Faltam SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET no .env.\n"
            "Veja o README.md -> 'Configuração' para criar seu app gratuito."
        )

    cache_path = f".cache-flowlist-{account}" if account else ".cache-flowlist"

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_path=cache_path,
        open_browser=True,
    )
    # status_retries=0 é de propósito: o padrão do spotipy, ao levar um 429,
    # DORME o tempo exato que o header Retry-After mandar antes de tentar de
    # novo — e a Spotify já mandou valores de mais de 20 HORAS pra esse tipo
    # de app. Preferível falhar na hora com uma mensagem clara (ver cli.py)
    # do que o programa travar em silêncio o dia inteiro.
    return spotipy.Spotify(auth_manager=auth_manager, retries=1, status_retries=0)


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

    Nota: os endpoints "vários de uma vez" (`/v1/albums?ids=`, `/v1/tracks?ids=`)
    voltam 403 pra apps novos (mesma família de restrição do audio-features).
    Então isso busca álbum por álbum e faixa por faixa — mais lento, mas é o
    que de fato funciona hoje. Também: `artist_albums` só aceita limit<=10
    pra esses apps (o documentado é 50); usar mais que isso também dá 400.
    """
    artist_id, artist_name_resolved = find_artist_id(sp, artist_name)

    album_ids: list[str] = []
    seen_album_names: set[str] = set()
    results = sp.artist_albums(artist_id, include_groups="album,single", country="US", limit=10)
    while results:
        for album in results["items"]:
            key = normalize_title(album["name"])
            if key in seen_album_names:
                continue
            seen_album_names.add(key)
            album_ids.append(album["id"])
        results = sp.next(results) if results.get("next") else None

    track_ids: list[str] = []
    for album_id in album_ids:
        page = sp.album_tracks(album_id, limit=50)
        while page:
            track_ids.extend(t["id"] for t in page["items"] if t and t.get("id"))
            page = sp.next(page) if page.get("next") else None

    print(
        f"{len(album_ids)} álbuns/singles, {len(track_ids)} faixas encontradas. "
        "Buscando detalhes uma a uma (a API deste app não permite busca em lote)..."
    )
    full_tracks: list[dict] = []
    for i, tid in enumerate(track_ids, 1):
        try:
            full_tracks.append(sp.track(tid))
        except SpotifyException as e:
            print(f"  ⚠ falhou ao buscar uma faixa: {e}")
        if i % 25 == 0 or i == len(track_ids):
            print(f"  ... {i}/{len(track_ids)}")

    # Deduplica por título normalizado. Prioridade: 1) versão "limpa" (sem
    # sufixo de live/remix/etc.) sempre que ela existir, 2) maior popularidade
    # como desempate. Sem isso, um álbum ao vivo recém-lançado (que ganha um
    # boost de popularidade só por ser novo) atropelava as faixas de estúdio
    # clássicas na hora de escolher as "melhores" — não é isso que se espera
    # de um "melhores da discografia".
    def _is_clean(t: dict) -> bool:
        return t["name"].strip().lower() == normalize_title(t["name"])

    best_by_title: dict[str, dict] = {}
    for t in full_tracks:
        key = normalize_title(t["name"])
        current = best_by_title.get(key)
        if current is None:
            best_by_title[key] = t
            continue
        t_clean, cur_clean = _is_clean(t), _is_clean(current)
        if t_clean and not cur_clean:
            best_by_title[key] = t
        elif t_clean == cur_clean and t.get("popularity", 0) > current.get("popularity", 0):
            best_by_title[key] = t

    ranked = sorted(best_by_title.values(), key=lambda t: t.get("popularity", 0), reverse=True)
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
