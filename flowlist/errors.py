"""
Tradução de erros conhecidos pra mensagens que fazem sentido pra quem tá
usando — compartilhado entre o CLI e a GUI, pra não desalinhar as duas
mensagens com o tempo.
"""

from __future__ import annotations

from spotipy import SpotifyException
from spotipy.oauth2 import SpotifyOauthError


def _human_time(seconds: float) -> str:
    minutes = seconds / 60
    if minutes < 90:
        return f"~{minutes:.0f} min"
    return f"~{minutes / 60:.1f} h"


def describe_error(exc: Exception, env_file: str = ".env", cache_path: str = ".cache-flowlist") -> str:
    """Devolve uma mensagem amigável pros erros conhecidos que já apareceram
    na prática. Pra qualquer outra exceção, devolve só `str(exc)`."""
    if isinstance(exc, SpotifyOauthError):
        # Sintoma clássico: trocou o Client ID/Secret no mesmo --env-file,
        # mas o token/refresh_token salvo em cache ainda é do Client ID
        # antigo. A Spotify rejeita a renovação (invalid_client/invalid_grant)
        # porque o refresh_token não pertence a esse app.
        return (
            f"⛔ Erro de autenticação ({exc.error or 'erro'}): {exc.error_description or exc}\n"
            f"Se você trocou as credenciais em '{env_file}' recentemente, o token salvo "
            f"em '{cache_path}' pode ser de um Client ID antigo. Apague esse arquivo "
            "e tente de novo pra forçar um login novo."
        )

    if isinstance(exc, SpotifyException):
        if exc.http_status == 429:
            wait = exc.headers.get("Retry-After")
            wait_str = f" (a Spotify pediu pra esperar {_human_time(float(wait))})" if wait else ""
            return (
                f"⛔ Rate limit da Spotify atingido{wait_str}.\n"
                "Isso não é erro do programa — é um limite da própria Spotify. Testado na prática: "
                "ele segue a CONTA autenticada (não o app/Client ID nem a rede), então trocar de "
                "app não ajuda. Espere o tempo indicado e tente de novo."
            )
        if exc.http_status == 403:
            return (
                f"⛔ Acesso negado pela Spotify (403): {exc.msg}\n"
                "Pode ser um endpoint restrito pra esse tipo de app (ex.: audio-features, "
                "endpoints em lote) — o programa já contorna os casos conhecidos, então se "
                "isso apareceu de novo pode ser um caso novo. Rode com --debug-bpm ou confira "
                "o README (seção 'Limitações honestas')."
            )

    return str(exc)
