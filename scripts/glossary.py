#!/usr/bin/env python3
"""Proofread caption or script text against the crypto glossary (RULE 047).

Reports likely ticker/name mistakes. Never rewrites files.

Usage:
  python scripts/glossary.py words_C.json
  python scripts/glossary.py project_C.json
  python scripts/glossary.py --text "I need everything soul"
  python scripts/glossary.py LINE_SETS.md captions.srt
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# token (lowercase) -> (suggested, why)
CONFUSIONS = {
    "soul": ("SOL", "Solana ticker, not the English word 'soul'"),
    "souls": ("SOL", "Solana ticker"),
    "sohl": ("SOL", "Solana ticker"),
    "etherium": ("Ethereum", "common misspelling"),
    "ethereom": ("Ethereum", "common misspelling"),
    "salana": ("Solana", "common misspelling"),
    "solanna": ("Solana", "common misspelling"),
    "defy": ("DeFi", "DeFi, not 'defy'"),
    "validater": ("validator", "common misspelling"),
    "airdrop": None,  # already correct; listed for completeness
}

# lowercase forms that should appear in capitals when used as tickers
TICKERS = ("SOL", "ETH", "BTC", "USDC", "USDT", "USD", "NFT", "DAO", "L2")

# names we only flag when clearly misspelled (handled in CONFUSIONS)
SKIP_AS_WORD = {"airdrop"}


def _iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        # Prefer human-facing caption / script fields when present.
        prefer = ("text", "script", "word", "caption", "display", "line")
        hit = False
        for k in prefer:
            if k in obj and isinstance(obj[k], str):
                hit = True
                yield obj[k]
        if not hit:
            for v in obj.values():
                yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def extract_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        return "\n".join(_iter_strings(data))
    return raw


_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9']*")


def check_text(text: str, source: str = "text") -> list[dict]:
    findings: list[dict] = []
    for m in _TOKEN.finditer(text):
        token = m.group(0)
        low = token.lower()
        if low in SKIP_AS_WORD:
            continue
        if low in CONFUSIONS and CONFUSIONS[low]:
            suggested, why = CONFUSIONS[low]
            findings.append({
                "source": source,
                "found": token,
                "suggest": suggested,
                "why": why,
                "index": m.start(),
            })
            continue
        for ticker in TICKERS:
            if low == ticker.lower() and token != ticker:
                # Allow normal English "sold" etc. already handled; "eth"/"btc" mid-case.
                if ticker in {"SOL", "ETH", "BTC", "USDC", "USDT", "USD", "NFT", "DAO", "L2"}:
                    # Don't flag "sold", "soul" is already in CONFUSIONS.
                    if token.isalpha() and token.lower() not in {t.lower() for t in TICKERS}:
                        continue
                    findings.append({
                        "source": source,
                        "found": token,
                        "suggest": ticker,
                        "why": f"ticker should be written {ticker}",
                        "index": m.start(),
                    })
                break
    return findings


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__.strip(), file=sys.stderr)
        return 0 if argv else 2
    findings: list[dict] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--text":
            if i + 1 >= len(argv):
                print("glossary.py: --text needs a string", file=sys.stderr)
                return 2
            findings.extend(check_text(argv[i + 1], source="--text"))
            i += 2
            continue
        path = Path(argv[i])
        if not path.is_file():
            print(f"glossary.py: not a file: {path}", file=sys.stderr)
            return 2
        findings.extend(check_text(extract_text(path), source=str(path)))
        i += 1
    if not findings:
        print("glossary: OK (no known ticker/name issues)")
        return 0
    print(f"glossary: {len(findings)} possible issue(s)")
    for f in findings:
        print(f"  [{f['source']}] {f['found']!r} -> {f['suggest']}  ({f['why']})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
