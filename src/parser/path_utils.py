"""Filesystem path helpers for corpus files."""

from __future__ import annotations

import unicodedata
from pathlib import Path


def resolve_manifest_path(data_root: Path, relative_path: str) -> Path:
    """Return the existing path for a manifest-relative corpus path.

    Some unpacked Korean corpus folders use decomposed Hangul names while the
    manifest stores composed Hangul.  Try both normal forms without modifying
    the raw corpus.
    """
    root = Path(data_root)
    variants = [
        relative_path,
        unicodedata.normalize("NFC", relative_path),
        unicodedata.normalize("NFD", relative_path),
    ]
    seen = set()
    for variant in variants:
        if variant in seen:
            continue
        seen.add(variant)
        candidate = root / variant
        if candidate.exists():
            return candidate
    return root / relative_path
