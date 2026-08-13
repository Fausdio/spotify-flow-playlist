"""
Orquestra o fluxo completo (buscar faixas -> enriquecer com BPM/tom ->
ordenar -> opcionalmente criar a playlist), independente de quem chama
(CLI ou GUI). Print continua sendo o canal de "log" — quem quiser capturar
essas mensagens (a GUI, por exemplo) redireciona sys.stdout ao redor da
chamada; é o jeito mais simples de reusar spotify_client/ordering como
estão, sem precisar passar callback de log por todo canto.

`prepare()` e `publish()` são separados de propósito: uma "Pré-visualizar"
seguida de "Criar playlist" (fluxo normal da GUI) não deve rebuscar e
reenriquecer tudo de novo — isso já rodou na pré-visualização. `publish()`
só usa o resultado que já foi calculado; a única chamada nova à Spotify
é a de criar a playlist (e, se pedido, subir a capa).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import enrichment, ordering, spotify_client, track_cache
from .spotify_client import Track

DEFAULT_DESCRIPTION = "Gerada por flowlist — ordenada por BPM/tom pra crossfade contínuo."


@dataclass
class RunParams:
    artist: str | None = None
    playlist: str | None = None
    top: int = 30
    name: str | None = None
    description: str | None = None
    cover_image_path: str | None = None
    public: bool = False
    use_getsongbpm: bool = False
    debug_bpm: bool = False
    refresh_cache: bool = False
    only_with_bpm: bool = False
    update_in_place: bool = False  # só vale com --playlist: reordena a original em vez de criar cópia
    dry_run: bool = True


@dataclass
class RunResult:
    ordered_tracks: list[Track]
    default_name: str
    playlist_url: str | None = None  # None quando foi só preview (dry_run)


def prepare(sp, params: RunParams) -> RunResult:
    """Busca (ou usa cache) + enriquece com BPM/tom + ordena. Não cria nada
    na Spotify — é a parte "cara" (chamadas de fetch), separada de propósito
    de publish() pra poder ser reusada sem rebuscar."""
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

    return RunResult(ordered_tracks=ordered, default_name=default_name, playlist_url=None)


def publish(sp, result: RunResult, params: RunParams) -> str:
    """Cria a playlist de verdade a partir de um RunResult já calculado
    (por prepare()) — não busca nem enriquece nada de novo."""
    uris = [t.uri for t in result.ordered_tracks]

    if params.update_in_place:
        if not params.playlist:
            raise ValueError("update_in_place só funciona remixando uma playlist existente (--playlist).")
        url = spotify_client.replace_playlist_items(sp, params.playlist, uris)
        spotify_client.update_playlist_details(
            sp, params.playlist, name=params.name, description=params.description, public=None
        )
        verb, playlist_name = "atualizada", params.name or result.default_name
    else:
        playlist_name = params.name or result.default_name
        description = params.description or DEFAULT_DESCRIPTION
        url = spotify_client.create_playlist(sp, playlist_name, description, uris, public=params.public)
        verb = "criada"

    if params.cover_image_path:
        try:
            spotify_client.set_playlist_cover(sp, url, params.cover_image_path)
            print("🖼 Capa da playlist atualizada.")
        except Exception as e:  # noqa: BLE001 — não falha a criação/atualização por causa da capa
            print(f"⚠ Playlist {verb}, mas não consegui trocar a capa: {e}")

    print(f"✅ Playlist {verb}: {playlist_name}")
    print(f"   {url}\n")
    print(
        "Pra ouvir como um mix contínuo: no Spotify Premium, abra a playlist e toque "
        "Mixar -> Editar -> Smart Reorder (recurso nativo, faz a mixagem de verdade). "
        "Sem Premium, use Configurações -> Reprodução de música -> Crossfade no desktop."
    )
    return url


def run(sp, params: RunParams) -> RunResult:
    """Atalho prepare()+publish() num passo só — usado pelo CLI (que não
    precisa reusar preview, já é uma chamada única do início ao fim)."""
    result = prepare(sp, params)
    if params.dry_run:
        print("(pré-visualização: nada foi criado na sua conta Spotify)")
        return result
    result.playlist_url = publish(sp, result, params)
    return result
