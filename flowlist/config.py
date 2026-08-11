"""Helpers de configuração compartilhados entre o CLI e a GUI."""

from __future__ import annotations

import os


def derive_account(env_file: str, explicit_account: str | None = None) -> str | None:
    """Se --env-file for usado sem --account, cada arquivo de credenciais
    ganha seu próprio cache de token por padrão (evita misturar login de
    apps diferentes, ex.: .env.app2 -> cache com o apelido "app2")."""
    if explicit_account:
        return explicit_account
    if env_file == ".env":
        return None
    stem = os.path.basename(env_file).removeprefix(".env").strip(".")
    return stem or None


def cache_path_for(account: str | None) -> str:
    return f".cache-flowlist-{account}" if account else ".cache-flowlist"


def discover_env_files(directory: str = ".") -> list[str]:
    """Lista os arquivos .env* candidatos (exclui .env.example)."""
    found = []
    for name in sorted(os.listdir(directory)):
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            found.append(name)
    return found
