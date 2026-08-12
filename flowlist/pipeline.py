"""
Orquestra o fluxo completo (buscar faixas -> enriquecer com BPM/tom ->
ordenar -> opcionalmente criar a playlist), independente de quem chama
(CLI ou GUI). Print continua sendo o canal de "log" — quem quiser capturar
essas mensagens (a GUI, por exemplo) redireciona sys.stdout ao redor da
chamada; é o jeito mais simples de reusar spotify_client/ordering como
estão, sem precisar passar callback de log por todo canto.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import enrichment, ordering, spotify_client, track_cache
from .spotify_client import Track


@dataclass
class RunParams:
    artist: str | None = None
    playlist: str | None = None
    top: int = 30
    name: str | None = None
    public: bool = False
    use_getsongbpm: bool = False
    debug_bpm: bool = False
    refresh_cache: bool = False
    only_with_bpm: bool = False
    dry_run: bool = True


@dataclass
class RunResult:
    ordered_tracks: list[Track]
    default_name: str
    playlist_url: str | None = None  # None quando foi só preview (dry_run)


def run(sp, params: RunParams) -> RunResult:
    if not params.artist and not params.playlist:
        raise ValueError("Informe --artist ou --playlist (um dos dois).")
    if params.artist and params.playlist:
        raise ValueError("Use --artist OU --playlist, não os dois.")

    cache_key = params.artist or spotify_client.extract_playlist_id(params.playlist)
    cached = None if params.refresh_cache else track_cache.load(cache_key)

    source_name: str | None = None
    if cached:
        pool, source_name = cached
        print(
            f"📦 Usando {len(pool)} faixas do cache local de '{cache_key}' — nenhuma "
            "chamada à Spotify feita pra buscar faixas dessa vez. Marque 'Forçar busca "
            "nova' se quiser atualizar (ex.: a discografia mudou)."
        )
    elif params.artist:
        pool = spotify_client.get_artist_best_tracks(sp, params.artist)
    else:
        pool, source_name = spotify_client.get_playlist_tracks(sp, params.playlist)

    if not pool:
        raise RuntimeError("Nenhuma faixa encontrada. Confira o nome do artista / URL da playlist.")

    got_spotify_features = enrichment.enrich_with_spotify_audio_features(sp, pool)
    if not got_spotify_features and params.use_getsongbpm:
        filled = enrichment.enrich_with_getsongbpm(pool, debug=params.debug_bpm)
        print(f"getsongbpm.com completou {filled}/{len(pool)} faixas.")

    # Salva o POOL INTEIRO (não cortado por --top) — ver nota histórica em
    # spotify_client.get_artist_best_tracks sobre por que isso importa.
    track_cache.save(cache_key, pool, source_name=source_name)

    candidates = pool
    if params.only_with_bpm:
        # Nenhuma fonte de BPM (Spotify ou getsongbpm) cobre 100% de um
        # artista — é limitação real dos dados, não tem como forçar. Em vez
        # de devolver --top faixas com sobras sem BPM jogadas no fim, filtra
        # ANTES de cortar: a playlist final fica menor que o pedido (se
        # precisar), mas inteira mixável, sem nenhuma faixa "?" atrapalhando.
        with_bpm = [t for t in candidates if t.tempo]
        if len(with_bpm) < len(candidates):
            print(
                f"🎯 --only-with-bpm: usando só as {len(with_bpm)} faixas (de {len(candidates)}) "
                "que têm BPM/tom confirmado — a playlist final vai ficar com esse tanto, não "
                f"necessariamente {params.top}, mas 100% pronta pra mixar sem sobra no fim."
            )
        candidates = with_bpm

    tracks = candidates
    if params.artist and len(candidates) > params.top:
        print(f"Usando as {params.top} mais populares de {len(candidates)} faixas disponíveis.")
        tracks = candidates[: params.top]

    if not tracks:
        raise RuntimeError(
            "Nenhuma faixa com BPM/tom confirmado sobrou depois do --only-with-bpm. "
            "Tente sem essa opção, ou rode com --use-getsongbpm se ainda não tinha usado."
        )

    default_name = (
        f"{params.artist} — Non-Stop Mix" if params.artist else f"{source_name} (Flow Remix)"
    )

    ordered = ordering.build_flow(tracks)
    ordering.print_flow_report(ordered)

    if params.dry_run:
        print("(pré-visualização: nada foi criado na sua conta Spotify)")
        return RunResult(ordered_tracks=ordered, default_name=default_name, playlist_url=None)

    playlist_name = params.name or default_name
    description = "Gerada por flowlist — ordenada por BPM/tom pra crossfade contínuo."
    url = spotify_client.create_playlist(
        sp, playlist_name, description, [t.uri for t in ordered], public=params.public
    )

    print(f"✅ Playlist criada: {playlist_name}")
    print(f"   {url}\n")
    print(
        "Pra ouvir como um mix contínuo: no Spotify Premium, abra a playlist e toque "
        "Mixar -> Editar -> Smart Reorder (recurso nativo, faz a mixagem de verdade). "
        "Sem Premium, use Configurações -> Reprodução de música -> Crossfade no desktop."
    )
    return RunResult(ordered_tracks=ordered, default_name=default_name, playlist_url=url)
