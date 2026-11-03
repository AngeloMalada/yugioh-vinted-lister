"""Identify Yu-Gi-Oh! cards from photos: local OCR + fuzzy match against the official card list.

Finds the card name (top of the card) and the set code printed under the artwork
(e.g. MP25-EN201), which is turned into the set name (e.g. "2025 Mega-Pack Tin").
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests
from rapidfuzz import fuzz, process

DB_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
from paths import DB_CACHE  # {"sets": {name: [[code, set]]}, "ids": {passcode: name}}
DB_MAX_AGE = 7 * 24 * 3600  # refresh weekly

# Set codes look like LOB-EN005, MP25-EN201, SDK-001, DUNE-DE042 ...
SET_CODE_RE = re.compile(r"\b([A-Z0-9]{2,5})-([A-Z]{0,2})([0-9OIL]{3})\b")
# 8-digit passcode printed bottom-left, e.g. "58995660 1st Edition"
PASSCODE_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")

_ocr = None
_db: dict[str, list[list[str]]] | None = None
_ids: dict[str, str] = {}
_names: list[str] = []
_upper: list[str] = []


def _load_db() -> None:
    global _db, _ids, _names, _upper
    if _db is not None:
        return
    if not DB_CACHE.exists() or time.time() - DB_CACHE.stat().st_mtime > DB_MAX_AGE:
        print("Downloading Yu-Gi-Oh! card database (one-time, ~weekly)...")
        try:
            data = requests.get(DB_URL, timeout=120).json()["data"]
            db = {
                "sets": {c["name"]: [[s["set_code"], s["set_name"]] for s in c.get("card_sets", [])] for c in data},
                "ids": {str(img["id"]): c["name"] for c in data for img in c.get("card_images", [{"id": c["id"]}])},
            }
            DB_CACHE.write_text(json.dumps(db), encoding="utf-8")
        except Exception as e:  # offline: fall back to a stale cache if we have one
            if not DB_CACHE.exists():
                raise RuntimeError(f"Could not download card database: {e}")
            print(f"  (download failed, using cached copy: {e})")
    raw = json.loads(DB_CACHE.read_text(encoding="utf-8"))
    _db, _ids = raw["sets"], raw["ids"]
    _names = sorted(_db)
    _upper = [n.upper() for n in _names]


def _engine():
    global _ocr
    if _ocr is None:
        from rapidocr import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def _ocr_lines(image_path) -> list[tuple[str, float, float, float]]:
    """OCR the photo -> [(text, center_y, left_x, height)] sorted top to bottom."""
    res = _engine()(str(image_path))
    txts = list(res.txts or [])
    boxes = list(res.boxes) if res.boxes is not None else []
    items = []
    for i, t in enumerate(txts):
        if i < len(boxes):
            ys = [p[1] for p in boxes[i]]
            xs = [p[0] for p in boxes[i]]
            items.append((t.strip(), (min(ys) + max(ys)) / 2, min(xs), max(ys) - min(ys)))
        else:
            items.append((t.strip(), float(i), 0.0, 0.0))
    items.sort(key=lambda it: it[1])
    return items


def _match_name(items) -> tuple[str, float, str]:
    items = [it for it in items if sum(ch.isascii() and ch.isalpha() for ch in it[0]) >= 3]
    # Candidates: each line, plus adjacent pairs (titles sometimes get split into 2 boxes).
    # Boxes on the same row are joined left-to-right.
    candidates: list[tuple[str, int]] = []
    for i, a in enumerate(items):
        candidates.append((a[0], i))
        if i + 1 < len(items):
            b = items[i + 1]
            same_row = abs(a[1] - b[1]) < max(a[3], b[3]) / 2
            first_, second = (a, b) if not same_row or a[2] <= b[2] else (b, a)
            candidates.append((f"{first_[0]} {second[0]}", i))

    best = ("", 0.0, "")
    for text, idx in candidates:
        if len(text) > 70:  # card text / flavor text, not a title
            continue
        match = process.extractOne(text.upper(), _upper, scorer=fuzz.ratio)
        if not match:
            continue
        _, score, db_idx = match
        score -= idx * 0.5  # slight preference for lines near the top of the photo
        score += min(len(text), 40) * 0.05  # on ties, prefer the longer (more complete) name
        if score > best[1]:
            best = (_names[db_idx], score, text)
    return best[0], round(min(max(best[1], 0.0), 100.0), 1), best[2]


def _norm_code(code: str) -> str:
    """'MP25-EN201' -> 'MP25-201' (language letters ignored, so DE/FR/IT prints match too)."""
    m = SET_CODE_RE.search(code.upper())
    if not m:
        return code.upper()
    num = m.group(3).replace("O", "0").replace("I", "1").replace("L", "1")
    return f"{m.group(1)}-{num}"


def find_set(name: str, items) -> tuple[str, str]:
    """Return (set_name, set_code printed on the card) or ('', '') if unsure."""
    sets = _db.get(name, []) if name else []
    if not sets:
        return "", ""
    printed = [m.group(0) for it in items for m in SET_CODE_RE.finditer(it[0].upper().replace(" ", ""))]

    by_norm = {_norm_code(code): set_name for code, set_name in sets}
    for code in printed:
        if _norm_code(code) in by_norm:
            return by_norm[_norm_code(code)], code
    # OCR slightly off? Fuzzy-match against this card's known codes.
    for code in printed:
        hit = process.extractOne(_norm_code(code), list(by_norm), scorer=fuzz.ratio, score_cutoff=80)
        if hit:
            return by_norm[hit[0]], code
    # No readable code: only safe to guess if the card was printed in a single set.
    if len({s for _, s in sets}) == 1:
        return sets[0][1], ""
    return "", ""


_by_code: dict[str, list[tuple[str, str]]] | None = None


def _code_index() -> dict[str, list[tuple[str, str]]]:
    """normalized set code -> [(card name, set name)]"""
    global _by_code
    if _by_code is None:
        _by_code = {}
        for name, sets in _db.items():
            for code, set_name in sets:
                _by_code.setdefault(_norm_code(code), []).append((name, set_name))
    return _by_code


def cards_from_code(items) -> list[tuple[str, str, str]]:
    """Look up the printed set code(s) in the database -> [(card name, set name, printed code)]."""
    idx = _code_index()
    out = []
    for it in items:
        for m in SET_CODE_RE.finditer(it[0].upper().replace(" ", "")):
            for name, set_name in idx.get(_norm_code(m.group(0)), []):
                out.append((name, set_name, m.group(0)))
    return out


def _title_similarity(name: str, items) -> float:
    """How well does `name` match any text line (or adjacent pair) on the card?"""
    texts = [it[0] for it in items]
    texts += [f"{a} {b}" for a, b in zip(texts, texts[1:])] + [f"{b} {a}" for a, b in zip(texts, texts[1:])]
    return max((fuzz.ratio(name.upper(), t.upper()) for t in texts if len(t) <= 70), default=0.0)


def card_from_passcode(items) -> str:
    for it in items:
        for m in PASSCODE_RE.finditer(it[0].replace("O", "0").replace("o", "0")):
            if m.group(1) in _ids:
                return _ids[m.group(1)]
    return ""


def extract_card(image_path: str | Path) -> dict:
    """Identify a card three ways - title, printed set code, printed passcode - and cross-check.

    Returns {name, confidence, raw, set_name, set_code, check} where check is one of:
      verified              - the title agrees with the set code / passcode
      title only            - no set code or passcode could be read
      from set code - CHECK - title didn't match; name comes from code/passcode (foreign card, or
                              the database uses a different name than the print)
      MISMATCH ...          - clear title disagrees with the code; please look at this one
    """
    _load_db()
    items = _ocr_lines(image_path)
    name, conf, raw = _match_name(items) if items else ("", 0.0, "")
    by_code = cards_from_code(items)
    by_pass = card_from_passcode(items)

    # Identities from the printed numbers (a passcode agreeing with a set code is strongest).
    ids = [(n, s, c) for n, s, c in by_code if not by_pass or n == by_pass] or by_code
    if by_pass and not ids:
        set_name, set_code = find_set(by_pass, items)
        ids = [(by_pass, set_name, set_code)]

    if not ids:
        set_name, set_code = find_set(name, items)
        return {"name": name, "confidence": conf, "raw": raw, "set_name": set_name,
                "set_code": set_code, "check": "title only"}

    for id_name, set_name, code in ids:
        if id_name == name:
            return {"name": name, "confidence": 100.0, "raw": raw, "set_name": set_name,
                    "set_code": code, "check": "verified"}

    id_name, set_name, code = max(ids, key=lambda c: _title_similarity(c[0], items))
    sim = _title_similarity(id_name, items)
    if sim >= 75:
        return {"name": id_name, "confidence": sim, "raw": raw, "set_name": set_name,
                "set_code": code, "check": "verified"}
    if conf >= 80:
        set_name2, code2 = find_set(name, items)
        return {"name": name, "confidence": conf, "raw": raw, "set_name": set_name2, "set_code": code2,
                "check": f"MISMATCH: number {code or by_pass} is '{id_name}'"}
    # Best guess at the printed title for the review message: longest ALL-CAPS line without brackets/digits.
    caps = [it[0] for it in items if it[0].isupper() and not re.search(r"[\[\]/\d©,.]", it[0]) and " " in it[0]]
    return {"name": id_name, "confidence": conf, "raw": max(caps, key=len, default=""),
            "set_name": set_name, "set_code": code, "check": "from set code - CHECK"}
