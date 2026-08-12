from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from spotipy import SpotifyException
from spotipy.oauth2 import SpotifyOauthError

from . import config, errors, pipeline, spotify_client


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
    p.add_argument("--description", help="Descrição da playlist (padrão: um texto genérico explicando que foi gerada pelo flowlist)")
    p.add_argument(
        "--cover-image",
        help="Caminho de uma imagem JPEG (.jpg/.jpeg, até 256KB) pra usar como capa da playlist",
    )
    p.add_argument("--public", action="store_true", help="Cria a playlist como pública (padrão: privada)")
    p.add_argument("--dry-run", action="store_true", help="Só mostra a ordem sugerida, não cria nada no Spotify")
    p.add_argument("--use-getsongbpm", action="store_true",
                    help="Se a Spotify bloquear audio-features, tenta completar via getsongbpm.com (precisa de API key no .env)")
    p.add_argument("--debug-bpm", action="store_true", help="Mostra as respostas cruas da API de BPM")
    p.add_argument(
        "--only-with-bpm",
        action="store_true",
        help="Só inclui faixas com BPM/tom confirmado (filtra ANTES de aplicar --top) — a "
        "playlist final pode ficar com menos faixas que o pedido, mas fica inteira mixável, "
        "sem faixas sem BPM misturadas no fim",
    )
    p.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignora o cache local de faixas desse artista/playlist e busca tudo de novo na "
        "Spotify (o padrão é reusar o cache pra economizar chamadas e não bater rate limit)",
    )
    p.add_argument(
        "--account",
        help="Apelido pra guardar o login em cache separado (ex.: --account teste2), "
        "útil pra testar com mais de uma conta Spotify sem uma sobrescrever o token da outra",
    )
    p.add_argument(
        "--env-file",
        default=".env",
        help="Arquivo de credenciais a usar (padrão: .env). Útil pra alternar entre apps "
        "diferentes do Spotify Developer Dashboard — cada um com seu próprio rate limit — "
        "ex.: --env-file .env.app2",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv or sys.argv[1:])

    # override=True é de propósito: por padrão o load_dotenv NÃO sobrescreve
    # uma variável já existente no ambiente. Sem isso, um SPOTIFY_CLIENT_ID
    # deixado em alguma sessão de terminal anterior (ex.: um $env:... manual
    # ao debugar) faria o --env-file ser ignorado em silêncio — o objetivo
    # inteiro dessa flag é garantir determinismo sobre qual credencial é usada.
    if not load_dotenv(args.env_file, override=True):
        print(f"⚠ Não achei o arquivo '{args.env_file}' (ou ele está vazio). "
              "Conferindo variáveis de ambiente já existentes no sistema, se houver.")

    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    shown = f"{client_id[:8]}…" if client_id else "(vazio!)"
    print(f"🔑 Usando '{args.env_file}' — SPOTIFY_CLIENT_ID = {shown}")

    account = config.derive_account(args.env_file, args.account)
    cache_path = config.cache_path_for(account)

    params = pipeline.RunParams(
        artist=args.artist,
        playlist=args.playlist,
        top=args.top,
        name=args.name,
        description=args.description,
        cover_image_path=args.cover_image,
        public=args.public,
        use_getsongbpm=args.use_getsongbpm,
        debug_bpm=args.debug_bpm,
        refresh_cache=args.refresh_cache,
        only_with_bpm=args.only_with_bpm,
        dry_run=args.dry_run,
    )

    try:
        sp = spotify_client.get_spotify_client(account=account)
        pipeline.run(sp, params)
    except (SpotifyOauthError, SpotifyException) as e:
        raise SystemExit(errors.describe_error(e, env_file=args.env_file, cache_path=cache_path)) from None
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f"⛔ {e}") from None


if __name__ == "__main__":
    main()
