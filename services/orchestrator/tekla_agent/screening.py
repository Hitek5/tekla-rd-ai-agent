"""Prompt-injection / unsafe-intent screening with text normalisation.

The MVP matched blocked substrings against ``prompt.lower()``. Three easy bypasses:

1. **Unicode confusables** — "ехесute" with Cyrillic е/х/с looks identical but
   never matches the ASCII pattern "execute".
2. **Zero-width / invisible characters** — "exe​cute" defeats substring search.
3. **Language gap** — the corpus and users are Russian, but the blocklist was
   English-only, so "удали всё без подтверждения" sailed through.

Normalisation here is intentionally conservative — it is a *defence-in-depth*
screen in front of the policy layer and the human approval gate, not the only
control. We fold confusables to ASCII, strip invisibles, collapse whitespace, and
match patterns in both English and Russian on the normalised text.
"""

from __future__ import annotations

import re
import unicodedata

# Cyrillic (and a few Greek) letters that render identically to Latin ones.
# Folding them to Latin closes the homoglyph bypass for English patterns while
# Russian patterns are matched separately on the lower-cased original.
_CONFUSABLES = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
        "к": "k", "м": "m", "т": "t", "н": "h", "в": "b", "і": "i", "ј": "j",
        "ѕ": "s", "ԁ": "d", "ɡ": "g", "ο": "o", "ρ": "p", "ѵ": "v",
        "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "У": "Y", "Х": "X",
        "К": "K", "М": "M", "Т": "T", "Н": "H", "В": "B", "І": "I",
    }
)

# Invisible / formatting characters used to split keywords.
_INVISIBLES = re.compile(
    "[​‌‍‎‏⁠﻿­᠎‪-‮]"
)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Aggressively normalise text for safety matching (not for display)."""
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLES.sub("", text)
    text = text.casefold()
    folded = text.translate(_CONFUSABLES)
    return _WS.sub(" ", folded).strip()


def normalize_pair(text: str) -> tuple[str, str]:
    """Return (confusable-folded, original-casefolded) normalised variants.

    English/Latin patterns match the folded variant; Russian patterns must match
    the casefolded original, because folding would corrupt Cyrillic words.
    """
    base = unicodedata.normalize("NFKC", text)
    base = _INVISIBLES.sub("", base)
    base = base.casefold()
    base = _WS.sub(" ", base).strip()
    folded = base.translate(_CONFUSABLES)
    return folded, base


def compile_patterns(patterns: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    """Compile blocklist entries into word-boundary-ish regexes.

    Each pattern is normalised the same way as input. Spaces in a pattern match
    runs of whitespace; this keeps "delete  all" and "delete all" equivalent.
    """
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for raw in patterns:
        folded, _ = normalize_pair(raw)
        escaped = re.escape(folded).replace(r"\ ", r"\s+")
        compiled.append((raw, re.compile(escaped)))
    return compiled


def first_match(text: str, compiled: list[tuple[str, re.Pattern[str]]]) -> str | None:
    """Return the original label of the first pattern that matches, else None."""
    folded, base = normalize_pair(text)
    for label, pattern in compiled:
        if pattern.search(folded) or pattern.search(base):
            return label
    return None
