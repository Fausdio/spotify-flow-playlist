"""
Camada de acesso à API oficial do Spotify.

Tudo aqui usa endpoints documentados (spotipy). Nada de automação de
navegador — é mais robusto e não depende da UI do Spotify não mudar.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

import spotipy
from spotipy import SpotifyException
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from .text_utils import strip_noise_suffix

SCOPES = "playlist-modify-public playlist-modify-private playlist-read-private"

# Sem lote, get_artist_best_tracks dispara uma chamada por álbum e uma por
# faixa em sequência — sem pausa nenhuma, isso estoura o limite de rajada
# da Spotify (janela curta) mesmo num app novinho, antes de chegar perto de
# qualquer limite diário. Esse intervalo mantém o ritmo bem abaixo disso.
_REQUEST_DELAY = 0.15


def normalize_title(title: str) -> str:
    """Reduz o nome da faixa à 'versão base', pra deduplicar regravações
    (ex.: "Blood In The Cut" e "Blood In The Cut - Ojivolta Remix" viram a
    mesma chave — sem isso, cada remix/sessão/feat. vira uma entrada
    "única" separada na hora de escolher as melhores faixas)."""
    base = strip_noise_suffix(title)
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


def get_catalog_client() -> spotipy.Spotify:
    """Client Credentials Flow: só Client ID/Secret, sem login de usuário
    nenhum. Só alcança dados públicos de catálogo (busca, álbum, faixa) —
    nada de playlists, biblioteca ou qualquer coisa específica de usuário.

    Existe porque o rate limit severo que vimos na prática parece seguir a
    CONTA autenticada (mesmo bloqueio em Client IDs diferentes, mesma
    conta) — e aqui não tem conta nenhuma no meio, então é bem provável que
    escape desse limite. Útil pra buscar/cachear faixas sem depender de
    login enquanto uma conta está bloqueada. Não serve pra criar playlist.
    """
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit(
            "Faltam SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET no .env.\n"
            "Veja o README.md -> 'Configuração' para criar seu app gratuito."
        )
    auth_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
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
    result = sp.search(q=f"artist:{artist_name}", type="artist", limit=10)
    items = result["artists"]["items"]
    if not items:
        raise SystemExit(f"Nenhum artista encontrado para '{artist_name}'.")

    # Nem busca nem lookup individual de artista trazem 'followers'/
    # 'popularity' pra esse tipo de app (restrição descoberta na prática,
    # não documentada) — então "pegar o mais seguido" nunca funcionou de
    # verdade, sempre caía no primeiro resultado da busca, às vezes um
    # artista errado/obscuro com nome parecido. Prioriza nome exatamente
    # igual ao pedido (sem diferenciar maiúscula/minúscula); só cai pro
    # primeiro resultado (ordem de relevância da própria Spotify) se
    # nenhum bater exato.
    query = artist_name.strip().casefold()
    exact = [a for a in items if a["name"].strip().casefold() == query]
    best = exact[0] if exact else items[0]
    if not exact and len(items) > 1:
        others = ", ".join(a["name"] for a in items[1:4])
        print(
            f"⚠ Nenhum artista chamado exatamente '{artist_name}' — usando "
            f"'{best['name']}' (resultado mais relevante da busca). Outras opções "
            f"encontradas: {others}. Se não for o artista certo, use o nome exato."
        )
    return best["id"], best["name"]


def get_artist_best_tracks(sp: spotipy.Spotify, artist_name: str) -> list[Track]:
    """Varre toda a discografia (álbuns + singles), remove versões duplicadas
    e devolve TODAS as faixas únicas, ordenadas da mais pra menos popular —
    cobrindo a carreira inteira, não só o Top 10 (limite do endpoint
    artist-top-tracks). Quem decide quantas usar é quem chama (cli.py, via
    --top), aplicado DEPOIS — inclusive em cima do cache. Cortar aqui dentro
    já causou bug: um cache salvo com --top 50 ficava travado em 50 faixas
    pra sempre, mesmo pedindo --top 100 depois.

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
        time.sleep(_REQUEST_DELAY)

    print(
        f"{len(album_ids)} álbuns/singles, {len(track_ids)} faixas encontradas. "
        "Buscando detalhes uma a uma (a API deste app não permite busca em lote)..."
    )
    full_tracks: list[dict] = []
    for i, tid in enumerate(track_ids, 1):
        try:
            full_tracks.append(sp.track(tid))
        except SpotifyException as e:
            if e.http_status == 429:
                # Insistir só pioraria (cada tentativa reforça o backoff do
                # lado da Spotify). Para aqui com o que já foi buscado, em
                # vez de martelar as ~centenas de faixas restantes.
                print(f"  ⛔ rate limit no meio da busca ({i}/{len(track_ids)} faixas obtidas). Parando.")
                raise
            print(f"  ⚠ falhou ao buscar uma faixa: {e}")
        if i % 25 == 0 or i == len(track_ids):
            print(f"  ... {i}/{len(track_ids)}")
        time.sleep(_REQUEST_DELAY)

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
    print(f"Artista: {artist_name_resolved} — {len(ranked)} faixas únicas encontradas no total.")
    return [_track_from_full_object(t) for t in ranked]


def extract_playlist_id(playlist_url_or_id: str) -> str:
    match = re.search(r"playlist/([A-Za-z0-9]+)", playlist_url_or_id)
    return match.group(1) if match else playlist_url_or_id


def get_playlist_tracks(sp: spotipy.Spotify, playlist_url_or_id: str) -> tuple[list[Track], str]:
    playlist_id = extract_playlist_id(playlist_url_or_id)
    playlist = sp.playlist(playlist_id, fields="name,owner.id,tracks.items(track),tracks.next")
    name = playlist["name"]

    # Desde a migração de fev/2026, a Spotify só devolve os itens completos
    # de uma playlist que você é dono ou colaborador — pra qualquer outra
    # (inclusive playlists públicas de terceiros), o campo "tracks" some da
    # resposta sem erro nenhum (confirmado testando na prática). Então
    # --playlist só funciona com playlists suas por enquanto.
    if "tracks" not in playlist:
        raise SystemExit(
            f"⛔ Não consigo ler as faixas de '{name}' — a Spotify só devolve os "
            "itens completos de playlists que você é dono ou colaborador (restrição "
            "deles desde fev/2026, não é bug daqui). Use uma playlist sua, ou clone a "
            "playlist de terceiro pra sua conta antes de rodar o --playlist nela."
        )

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
    # current_user_playlist_create -> POST /v1/me/playlists. NÃO usar
    # user_playlist_create (POST /v1/users/{id}/playlists): a Spotify
    # descontinuou esse endpoint na migração de fev/2026 e, desde 09/mar/2026,
    # ele só devolve 403 — confirmado testando na prática.
    playlist = sp.current_user_playlist_create(name, public=public, description=description)
    for i in range(0, len(uris), 100):  # add_items aceita no máx. 100 por chamada
        sp.playlist_add_items(playlist["id"], uris[i : i + 100])
    return playlist["external_urls"]["spotify"]
