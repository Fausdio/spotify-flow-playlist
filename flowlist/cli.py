from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv
from spotipy import SpotifyException

from . import enrichment, ordering, spotify_client


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Cria/remixa playlists do Spotify em ordem de mixagem de DJ "
        "(BPM + tom compatíveis), pra usar com o crossfade do app desktop."
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--artist", help="Nome do artista (melhores faixas de toda a discografia)")
    source.add_argument("--playlist", help="URL ou ID de uma playlist existente, pra remixar a ordem")

    p.add_argument("--top", type=int, default=30, help="Quantas faixas incluir (padrão: 30, só com --artist)")
    p.add_argument("--name", help="Nome da playlist criada (padrão: gerado automaticamente)")
    p.add_argument("--public", action="store_true", help="Cria a playlist como pública (padrão: privada)")
    p.add_argument("--dry-run", action="store_true", help="Só mostra a ordem sugerida, não cria nada no Spotify")
    p.add_argument("--use-getsongbpm", action="store_true",
                    help="Se a Spotify bloquear audio-features, tenta completar via getsongbpm.com (precisa de API key no .env)")
    p.add_argument("--debug-bpm", action="store_true", help="Mostra as respostas cruas da API de BPM")
    return p.parse_args(argv)


def _human_time(seconds: float) -> str:
    minutes = seconds / 60
    if minutes < 90:
        return f"~{minutes:.0f} min"
    return f"~{minutes / 60:.1f} h"


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parse_args(argv or sys.argv[1:])

    sp = spotify_client.get_spotify_client()

    try:
        _run(sp, args)
    except SpotifyException as e:
        if e.http_status == 429:
            wait = e.headers.get("Retry-After")
            wait_str = f" (a Spotify pediu pra esperar {_human_time(float(wait))})" if wait else ""
            raise SystemExit(
                f"⛔ Rate limit da Spotify atingido pra este app{wait_str}.\n"
                "Isso não é erro do programa — é um limite da própria Spotify pra apps novos "
                "(bem mais apertado que o documentado). Espera um pouco e roda de novo; "
                "evite rodar em sequência rápida enquanto estiver testando."
            ) from None
        raise


def _run(sp, args: argparse.Namespace) -> None:
    if args.artist:
        tracks = spotify_client.get_artist_best_tracks(sp, args.artist, args.top)
        default_name = f"{args.artist} — Non-Stop Mix"
    else:
        tracks, original_name = spotify_client.get_playlist_tracks(sp, args.playlist)
        default_name = f"{original_name} (Flow Remix)"

    if not tracks:
        raise SystemExit("Nenhuma faixa encontrada. Confira o nome do artista / URL da playlist.")

    got_spotify_features = enrichment.enrich_with_spotify_audio_features(sp, tracks)
    if not got_spotify_features and args.use_getsongbpm:
        filled = enrichment.enrich_with_getsongbpm(tracks, debug=args.debug_bpm)
        print(f"getsongbpm.com completou {filled}/{len(tracks)} faixas.")

    ordered = ordering.build_flow(tracks)
    ordering.print_flow_report(ordered)

    if args.dry_run:
        print("(--dry-run: nada foi criado na sua conta Spotify)")
        return

    playlist_name = args.name or default_name
    description = "Gerada por flowlist — ordenada por BPM/tom pra crossfade contínuo."
    url = spotify_client.create_playlist(
        sp, playlist_name, description, [t.uri for t in ordered], public=args.public
    )

    print(f"✅ Playlist criada: {playlist_name}")
    print(f"   {url}\n")
    print(
        "Pra ouvir como um mix contínuo: abra o Spotify DESKTOP -> Configurações -> "
        "Reprodução de música -> ative 'Crossfade das músicas' (arraste pro máximo) "
        "e, logo abaixo, ative 'Automix'. Isso não dá pra automatizar (não existe "
        "no player web nem é exposto por nenhuma API) — é um toggle único, depois "
        "vale pra qualquer playlist que você tocar."
    )


if __name__ == "__main__":
    main()
