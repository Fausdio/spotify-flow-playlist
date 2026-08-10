"""
Monta a ordem das faixas pensando em mixagem de DJ: BPM real vizinho e
tom compatível na roda de Camelot.

Importante: o crossfade (e o Automix) do Spotify é só um fade de VOLUME
entre uma faixa e outra — ele não estica/comprime tempo nem faz
beatmatching como um mixer de DJ de verdade. Por isso a ordenação usa o
BPM real de cada faixa (não uma versão "dobrada"/"em meio-tempo"): um
salto de 85 pra 174 BPM vai soar como um salto mesmo que os números
sejam proporcionais, porque o Spotify não vai sincronizar as batidas.

Isso É a parte que dá pra automatizar. O crossfade em si continua sendo
um toggle manual no Spotify desktop — nenhuma API expõe esse controle.
"""

from __future__ import annotations

from .enrichment import key_mode_to_camelot
from .spotify_client import Track


def _fold_bpm(bpm: float) -> float:
    # Mantido como BPM real (sem dobrar/reduzir): ver nota no topo do arquivo.
    return bpm


def _camelot_distance(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 2.5  # penalidade neutra: não sabemos, não favorece nem prejudica muito
    if a == b:
        return 0.0
    num_a, letter_a = int(a[:-1]), a[-1]
    num_b, letter_b = int(b[:-1]), b[-1]
    if letter_a == letter_b:
        diff = min((num_a - num_b) % 12, (num_b - num_a) % 12)
        if diff == 1:
            return 0.5  # vizinho na roda -> mixa bem
        return 1.5 + diff * 0.2
    if num_a == num_b:
        return 0.7  # relativa maior/menor -> mixa bem
    return 3.0


def _pair_weight(a: Track, b: Track) -> float:
    bpm_a, bpm_b = _fold_bpm(a.tempo), _fold_bpm(b.tempo)
    bpm_term = abs(bpm_a - bpm_b)
    camelot_a = key_mode_to_camelot(a.key, a.mode) if a.key is not None else None
    camelot_b = key_mode_to_camelot(b.key, b.mode) if b.key is not None else None
    # Peso baixo de propósito: o crossfade do Spotify é só fade de volume
    # (sem pitch-shift), então um choque de tom é bem mais sutil ao ouvido
    # do que um salto de andamento. BPM tem que mandar na ordenação; tom
    # só desempata entre opções de BPM parecido.
    key_term = _camelot_distance(camelot_a, camelot_b) * 1.5
    return bpm_term + key_term


def build_flow(tracks: list[Track]) -> list[Track]:
    """Ordena as faixas num arco: abertura calma -> construção -> pico."""
    with_bpm = [t for t in tracks if t.tempo]
    without_bpm = [t for t in tracks if not t.tempo]

    if not with_bpm:
        print(
            "⚠ Nenhuma faixa teve BPM/tom disponível — sem dados de mixagem "
            "harmônica pra usar. Mantendo uma ordem por popularidade crescente "
            "(introdução mais discreta -> hits no final)."
        )
        return sorted(tracks, key=lambda t: t.popularity)

    if without_bpm:
        print(
            f"⚠ {len(without_bpm)} faixa(s) sem BPM/tom disponível — "
            "vão ser encaixadas ao final, ordenadas por popularidade."
        )

    remaining = with_bpm[:]
    ordered = [min(remaining, key=lambda t: _fold_bpm(t.tempo))]
    remaining.remove(ordered[0])

    while remaining:
        last = ordered[-1]
        nxt = min(remaining, key=lambda t: _pair_weight(last, t))
        ordered.append(nxt)
        remaining.remove(nxt)

    ordered.extend(sorted(without_bpm, key=lambda t: t.popularity, reverse=True))
    return ordered


def print_flow_report(tracks: list[Track]) -> None:
    print("\n" + "=" * 78)
    print(f"{'#':<3} {'Faixa':<38} {'BPM':>6} {'Tom':>7}  Transição")
    print("-" * 78)
    prev = None
    for i, t in enumerate(tracks, 1):
        bpm_str = f"{t.tempo:.0f}" if t.tempo else "?"
        camelot = key_mode_to_camelot(t.key, t.mode) if t.key is not None else None
        camelot_str = camelot or "?"
        note = ""
        if prev and prev.tempo and t.tempo:
            delta = abs(_fold_bpm(prev.tempo) - _fold_bpm(t.tempo))
            if delta <= 3:
                note = "≈ mesmo BPM, mix direto"
            elif delta <= 10:
                note = "BPM próximo, transição suave"
            else:
                note = "salto de BPM, use crossfade mais longo aqui"
        name = t.name if len(t.name) <= 38 else t.name[:35] + "..."
        print(f"{i:<3} {name:<38} {bpm_str:>6} {camelot_str:>7}  {note}")
        prev = t
    print("=" * 78 + "\n")
