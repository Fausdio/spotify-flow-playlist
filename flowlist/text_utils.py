"""
Limpeza de título compartilhada — usada tanto pra deduplicar regravações
na Spotify (spotify_client.normalize_title) quanto pra montar variantes de
busca na getsongbpm.com (enrichment._title_variants). Era duas regex quase
iguais em arquivos diferentes; consolidado aqui pra não desalinhar.
"""

from __future__ import annotations

import re

# As palavras-chave que marcam uma versão "não-canônica" de uma faixa. O
# lookahead (?=.*\bKEYWORD\b) casa a palavra em QUALQUER lugar depois do
# separador — não só logo em seguida — pra pegar casos como
# "- Wankelmut & Fynn Remix" ou "- Seattle Sessions" (o nome do
# remixer/sessão vem ANTES da palavra-chave).
_NOISE_KEYWORDS = (
    r"feat\.?|featuring|with|live|ao vivo|acoustic|acústic\w+|remix|"
    r"remaster\w*|radio edit|edit|mono|stereo|deluxe|bonus track|"
    r"mtv unplugged|karaoke|instrumental|sped up|slowed|version|sessions?|demo|extended"
)
_PAREN_NOISE = re.compile(rf"\s*[\(\[][^\)\]]*\b(?:{_NOISE_KEYWORDS})\b[^\)\]]*[\)\]]", re.IGNORECASE)
_DASH_NOISE = re.compile(rf"\s*[-–]\s*(?:(?!\s[-–]\s).)*\b(?:{_NOISE_KEYWORDS})\b.*$", re.IGNORECASE)


def strip_noise_suffix(name: str) -> str:
    """Remove sufixos de versão não-canônica (live, remix, feat., session,
    etc.), em parênteses ou depois de traço, onde quer que a palavra-chave
    apareça no trecho. Não mexe no resto do título."""
    cleaned = _PAREN_NOISE.sub("", name)
    cleaned = _DASH_NOISE.sub("", cleaned)
    return cleaned.strip()


def strip_all_parens(name: str) -> str:
    """Último recurso: tira QUALQUER parênteses/colchete, seja qual for o
    conteúdo (ex.: "T-Rex (from the Netflix Film ...)")."""
    bare = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", name)
    return re.sub(r"\s{2,}", " ", bare).strip()


def title_variants(name: str) -> list[str]:
    """Do título mais específico pro mais genérico, em ordem de tentativa."""
    variants = [name]
    cleaned = strip_noise_suffix(name)
    if cleaned and cleaned not in variants:
        variants.append(cleaned)
    bare = strip_all_parens(name)
    if bare and bare not in variants:
        variants.append(bare)
    return variants
