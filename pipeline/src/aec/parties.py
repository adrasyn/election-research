"""Party registry — loads `parties.yaml` and maps AEC codes to CSS keys.

The dashboard's chart generators (design/lib/*.js) all expect a lowercase
party key like 'alp' / 'lib' / 'grn' that resolves a CSS variable
(`var(--alp)`). This module is the single bridge between AEC's PartyAb
field and that key.

Independents (PartyAb empty or 'IND') always map to 'ind'.
Anything we don't recognise maps to 'oth' (rendered in the grey fallback).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# Repo root has parties.yaml; pipeline lives one directory deeper.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PARTIES_YAML = _REPO_ROOT / "parties.yaml"


@lru_cache(maxsize=1)
def _registry() -> dict[str, dict[str, Any]]:
    """Load parties.yaml once and index by every known AEC code / alias.

    YAML 1.1 (which PyYAML defaults to) parses unquoted ``ON``, ``OFF``,
    ``YES``, ``NO`` as booleans — the so-called "Norway problem". Several
    AEC codes hit this (notably ``ON`` for One Nation), so we coerce
    every key to ``str`` before normalising.
    """
    if not _PARTIES_YAML.exists():
        raise FileNotFoundError(f"parties.yaml not found at {_PARTIES_YAML}")
    raw = yaml.safe_load(_PARTIES_YAML.read_text())
    by_code: dict[str, dict[str, Any]] = {}
    for entry in raw.get("parties", []):
        # Resurrect any bool-mangled codes back to their string form.
        for field in ("aec_code", "abbr"):
            if isinstance(entry.get(field), bool):
                entry[field] = "ON" if entry[field] else "OFF"
        keys: set[str] = set()
        if entry.get("aec_code"):
            keys.add(str(entry["aec_code"]).upper())
        if entry.get("abbr"):
            keys.add(str(entry["abbr"]).upper())
        for alias in entry.get("aliases") or []:
            keys.add(str(alias).upper())
        for k in keys:
            by_code.setdefault(k, entry)
    return by_code


def css_key(aec_code: str | None) -> str:
    """Map an AEC PartyAb to the lowercase CSS key used by the frontend."""
    if not aec_code or not aec_code.strip():
        return "ind"
    code = aec_code.strip().upper()
    if code == "IND":
        return "ind"
    entry = _registry().get(code)
    if entry is None:
        return "oth"
    return str(entry.get("abbr", "oth")).lower()


def display_name(aec_code: str | None) -> str:
    """Return the registry's `display` name for an AEC code, or fall back."""
    if not aec_code:
        return "Independent"
    entry = _registry().get(aec_code.strip().upper())
    if entry is None:
        return aec_code
    return str(entry.get("display") or aec_code)


def display_short(aec_code: str | None) -> str:
    """Return the canonical short abbreviation for display.

    Normalises state-branch codes (e.g. GVIC → GRN). Falls back to the
    raw AEC PartyAb if the registry doesn't recognise the code.
    """
    if not aec_code or not aec_code.strip():
        return "IND"
    code = aec_code.strip().upper()
    if code == "IND":
        return "IND"
    entry = _registry().get(code)
    if entry is None:
        return code
    return str(entry.get("abbr") or code).upper()
