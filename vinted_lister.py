"""
Yu-Gi-Oh! -> Vinted draft lister.

1. Reads card photos from a folder and works out each card's name (OCR + official card list).
2. Writes them to names.csv so you can check/fix names.
3. Drives YOUR visible Chrome (via remote debugging) to create one Vinted listing per card:
     title "yugioh-<name>", description "<name>
<set name> (<set code>)", price 1 EUR, photo attached -> Save as draft.

Usage:
    python vinted_lister.py                 # extract names, review, then post drafts
    python vinted_lister.py --extract-only  # just build names.csv
    python vinted_lister.py --post-only     # skip extraction, post from existing names.csv
    python vinted_lister.py --watch         # stay connected (one Chrome 'Allow'), draft photos as they arrive
    python vinted_lister.py --inspect       # open the Vinted form and dump its fields (debugging)
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"}
from paths import NAMES_CSV, DONE_FILE, DEFAULT_CARDS

# Each field tries these selectors in order (Vinted changes its markup now and then).
SEL = {
    "photo_input": ['input[type="file"][accept*="image"]', 'input[type="file"]'],
    "title": ['[data-testid="title--input"]', 'input#title', 'input[name="title"]'],
    "description": ['[data-testid="description--input"]', 'textarea#description', 'textarea[name="description"]', "textarea"],
    "price": ['[data-testid="price-input--input"]', 'input#price', 'input[name="price"]'],
    "save_draft": ['[data-testid="upload-form-save-draft-button"]', 'button[data-testid*="draft"]'],
    # Appears once Vinted has auto-picked the category from the photo.
    "condition": ['[data-testid="category-condition-single-list-input"]', '[data-testid*="condition"][data-testid$="-input"]'],
}
# Fallback: match the "save draft" button by its label in common Vinted languages.
DRAFT_TEXT = re.compile(r"draft|skic|nacrt|brouillon|entwurf|bozza|borrador|rascunho|concept|szkic|koncept|piszkozat|ciorn", re.I)


# ----------------------------------------------------------------------------- extraction
def find_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS)


CSV_FIELDS = ["image", "name", "set_name", "set_code", "check", "confidence"]


def needs_review(row: dict) -> bool:
    """Cards that watch mode holds back until you fix them in names.csv (and set check to 'ok')."""
    check = (row.get("check") or "").strip()
    if not row.get("name", "").strip() or check == "skipped":
        return True
    if check in ("ok", "verified", "hand-edited name"):
        return False
    if check.startswith("MISMATCH") or check.endswith("CHECK"):
        return True
    return float(row.get("confidence") or 0) < 80


def extract_all(folder: Path, watch: bool = False) -> list[dict]:
    """OCR any photos not yet in names.csv, rewrite names.csv, return its rows.

    watch=True: quiet about already-known cards, skip photos still being copied in.
    """
    from card_names import extract_card

    images = find_images(folder)
    if watch:  # a photo modified in the last few seconds may still be copying
        images = [p for p in images if time.time() - p.stat().st_mtime > 3]
    if not images and not watch:
        sys.exit(f"No images found in {folder}")

    existing = {r["image"]: r for r in read_csv()} if NAMES_CSV.exists() else {}
    if watch and all(str(p.resolve()) in existing for p in images):
        return list(existing.values())  # nothing new - don't touch names.csv (you may be editing it)
    rows = []
    if not watch:
        print(f"Reading {len(images)} card photo(s)...")
    for i, img in enumerate(images, 1):
        key = str(img.resolve())
        old = existing.get(key)
        if old and old.get("name") and "check" in old:
            rows.append(old)  # keep earlier (possibly hand-edited) result
            if not watch:
                print(f"  [{i}/{len(images)}] {img.name}: {old['name']} | {old['set_name'] or '(no set)'}  (from names.csv)")
            continue
        if watch and old and "check" in old:
            rows.append(old)  # known card with a blank name (= you chose to skip it)
            continue
        card = extract_card(img)
        if old and old.get("name") and old["name"] != card["name"]:
            # names.csv from an older version: keep the hand-checked name, just look up its set.
            from card_names import _ocr_lines, find_set
            card["name"], card["confidence"] = old["name"], float(old.get("confidence") or 100)
            card["set_name"], card["set_code"] = find_set(card["name"], _ocr_lines(img))
            card["check"] = "hand-edited name"
        flags = []
        if card["check"].startswith("MISMATCH"):
            flags.append(card["check"])
        elif card["check"].endswith("CHECK"):
            flags.append(f"CHECK NAME - photo title looks like {card['raw']!r}" if card["raw"] else "CHECK NAME - title unreadable")
        elif card["confidence"] < 80 and card["check"] != "verified":
            flags.append("CHECK NAME")
        if not card["set_name"]:
            flags.append("NO SET FOUND")
        print(f"  {'new photo' if watch else f'[{i}/{len(images)}]'} {img.name}: {card['name'] or '???'} | "
              f"{card['set_name'] or '?'} {('(' + card['set_code'] + ')') if card['set_code'] else ''}  "
              f"[{card['check']}]" + (f"  <-- {', '.join(flags)}" if flags else ""))
        rows.append({"image": key, "name": card["name"], "set_name": card["set_name"],
                     "set_code": card["set_code"], "check": card["check"], "confidence": f"{card['confidence']:.0f}"})

    write_csv(rows)
    if not watch:
        print(f"\nSaved {len(rows)} card(s) to {NAMES_CSV}")
    return rows


def write_csv(rows: list[dict]) -> None:
    for attempt in range(10):
        try:
            with NAMES_CSV.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            return
        except PermissionError:  # names.csv open in Excel - wait and retry
            if attempt == 0:
                print("  (names.csv is locked - close it in Excel so I can save new cards)")
            time.sleep(3)
    raise RuntimeError("names.csv is locked by another program")


def description_for(row: dict) -> str:
    """Card name, then set name (+ printed set code) on the next line."""
    name = row["name"].strip()
    set_name = (row.get("set_name") or "").strip()
    set_code = (row.get("set_code") or "").strip()
    if not set_name:
        return name
    return f"{name}\n{set_name}" + (f" ({set_code})" if set_code else "")


def read_csv() -> list[dict]:
    with NAMES_CSV.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ----------------------------------------------------------------------------- browser
CHROME_PORT_FILE = Path.home() / "AppData/Local/Google/Chrome/User Data/DevToolsActivePort"
ENABLE_HELP = (
    "In your normal Chrome, open  chrome://inspect/#remote-debugging  and turn on\n"
    "'Allow remote debugging for this browser instance'. Then rerun this script and\n"
    "click 'Allow' in the prompt Chrome shows."
)


def chrome_endpoint(cdp_url: str | None) -> str:
    """Find the websocket address of the user's already-running Chrome."""
    if cdp_url:
        return cdp_url
    if not CHROME_PORT_FILE.exists():
        sys.exit("Your Chrome isn't accepting connections yet.\n" + ENABLE_HELP)
    port, path = CHROME_PORT_FILE.read_text().split()[:2]
    return f"ws://127.0.0.1:{port}{path}"


def connect(cdp_url: str | None):
    from playwright.sync_api import sync_playwright

    endpoint = chrome_endpoint(cdp_url)
    print("Connecting to your Chrome (click 'Allow' if Chrome asks)...")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(endpoint, timeout=60000)
    except Exception as e:
        pw.stop()
        sys.exit(f"Could not connect to Chrome at {endpoint}.\n{ENABLE_HELP}\nDetails: {e}")
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    return pw, browser, ctx


def first(page, key: str, timeout: int = 15000):
    """Return the first selector for `key` that exists on the page."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for s in SEL[key]:
            loc = page.locator(s).first
            if loc.count():
                return loc
        page.wait_for_timeout(300)
    return None


def draft_button(page):
    btn = first(page, "save_draft", timeout=3000)
    if btn:
        return btn
    loc = page.get_by_role("button", name=DRAFT_TEXT).first
    return loc if loc.count() else None


def pause(page, lo: float, hi: float) -> None:
    """Random human-ish pause between lo and hi seconds."""
    page.wait_for_timeout(random.uniform(lo, hi) * 1000)


def human_type(page, loc, text: str) -> None:
    """Click into a field and type it key by key with uneven timing (instead of pasting instantly)."""
    loc.click()
    pause(page, 0.2, 0.6)
    loc.fill("")
    for ch in text:
        loc.press_sequentially(ch, delay=0)
        delay = random.uniform(0.05, 0.18)
        if ch == " " or random.random() < 0.04:  # occasional hesitation
            delay += random.uniform(0.1, 0.5)
        page.wait_for_timeout(delay * 1000)


CATEGORY = "Single trading cards"
CATEGORY_PATH = ["Hobbies & collectables", "Trading cards", CATEGORY]
BRAND = "Yu-Gi-Oh!"


def _dropdown_value(page, testid: str) -> str:
    loc = page.locator(f'[data-testid="{testid}--value"]')
    return loc.inner_text().strip() if loc.count() else ""


def ensure_category(page) -> None:
    """Vinted usually picks 'Single trading cards' from the photo; if it didn't, pick it ourselves."""
    for _ in range(16):  # give Vinted's own suggestion ~8 s
        if _dropdown_value(page, "catalog-select-dropdown-input") == CATEGORY:
            return
        page.wait_for_timeout(500)

    btn = page.locator('[data-testid="catalog-select-dropdown-input"]')
    content = page.locator('[data-testid="catalog-select-dropdown-content"]')
    btn.scroll_into_view_if_needed()
    btn.hover()
    pause(page, 0.3, 0.8)
    btn.click()
    pause(page, 0.6, 1.4)
    try:  # 1) search box
        search = content.get_by_placeholder(re.compile("category", re.I)).first
        if not search.count():
            search = content.locator("input[type=text]").first
        human_type(page, search, CATEGORY)
        pause(page, 1.0, 2.0)
        hit = content.get_by_text(CATEGORY, exact=True).filter(visible=True).first
        hit.wait_for(state="visible", timeout=6000)
        hit.hover()
        pause(page, 0.2, 0.6)
        hit.click()
    except Exception:  # 2) click through the tree
        if not content.count() or not content.is_visible():
            btn.click()
            pause(page, 0.6, 1.2)
        search = content.locator("input[type=text]").first
        if search.count():
            search.fill("")
        for step in CATEGORY_PATH:
            content.get_by_text(step, exact=True).filter(visible=True).first.click()
            pause(page, 0.6, 1.3)
    pause(page, 0.8, 1.6)
    got = _dropdown_value(page, "catalog-select-dropdown-input")
    if got != CATEGORY:
        raise RuntimeError(f"Couldn't set the category to '{CATEGORY}' (it shows '{got}').")
    ensure_brand(page)


def ensure_brand(page) -> None:
    """Best effort: fill brand 'Yu-Gi-Oh!' if Vinted left it empty. Never fails the listing."""
    try:
        btn = page.locator('[data-testid="brand-select-dropdown-input"]')
        btn.wait_for(state="visible", timeout=6000)
        if BRAND.lower() in _dropdown_value(page, "brand-select-dropdown-input").lower():
            return
        btn.click()
        pause(page, 0.6, 1.2)
        content = page.locator('[data-testid="brand-select-dropdown-content"]')
        scope = content if content.count() else page
        search = scope.locator("input[type=text], input[type=search]").filter(visible=True).first
        if search.count():
            human_type(page, search, BRAND)
            pause(page, 1.0, 2.0)
        opt = scope.get_by_text(BRAND, exact=True).filter(visible=True).last
        opt.wait_for(state="visible", timeout=5000)
        opt.click()
        pause(page, 0.5, 1.2)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        print("      (couldn't set brand - continuing without it)")


def set_condition(page, condition: str) -> None:
    """Open the Condition dropdown and pick `condition` (e.g. 'New without tags')."""
    btn = first(page, "condition", timeout=20000)
    if not btn:
        raise RuntimeError("Condition field didn't appear (Vinted hadn't picked a category yet).")
    value = page.locator('[data-testid="category-condition-single-list-input--value"]')
    if value.count() and value.inner_text().strip().lower() == condition.lower():
        return
    btn.scroll_into_view_if_needed()
    btn.hover()
    pause(page, 0.3, 0.9)
    btn.click()
    pause(page, 0.8, 1.8)
    # Option label, e.g. "New without tags" - pick the visible one, not the button's own text.
    opt = page.get_by_text(condition, exact=True).filter(visible=True).last
    try:
        opt.wait_for(state="visible", timeout=8000)
    except Exception:
        raise RuntimeError(f"Condition option '{condition}' not found in the dropdown.")
    opt.hover()
    pause(page, 0.2, 0.6)
    opt.click()
    pause(page, 0.6, 1.5)
    if value.count() and condition.lower() not in value.inner_text().lower():
        raise RuntimeError(f"Tried to set condition '{condition}' but it shows '{value.inner_text().strip()}'.")


def make_draft(page, site: str, image: str, name: str, description: str, price: str, condition: str) -> None:
    page.goto(f"{site}/items/new", wait_until="domcontentloaded")
    if "/signup" in page.url or "/login" in page.url or "member/signup" in page.url:
        raise RuntimeError("Not logged in to Vinted in this Chrome window. Log in, then rerun.")

    photo = first(page, "photo_input")
    if not photo:
        raise RuntimeError("Photo upload field not found (run with --inspect).")
    pause(page, 1.5, 4)  # "looking at" the page before picking a photo
    from image_prep import upload_ready
    photo.set_input_files(upload_ready(image))  # phone photos > 7 MB are shrunk to a copy first
    pause(page, 2, 5)  # let the thumbnail upload start

    title = first(page, "title")
    desc = first(page, "description")
    price_in = first(page, "price")
    for loc, label in ((title, "title"), (desc, "description"), (price_in, "price")):
        if not loc:
            raise RuntimeError(f"'{label}' field not found (run with --inspect).")

    human_type(page, title, f"yugioh-{name}")
    pause(page, 0.8, 2.5)
    human_type(page, desc, description)
    pause(page, 0.8, 2.5)
    human_type(page, price_in, price)
    price_in.press("Tab")
    pause(page, 0.8, 2)
    ensure_category(page)
    set_condition(page, condition)
    pause(page, 1, 3)

    # Wait for the photo upload to finish (Vinted shows a spinner/progress on thumbnails).
    page.wait_for_timeout(2000)
    try:
        page.wait_for_function(
            "() => !document.querySelector('[class*=\"progress\" i], [class*=\"spinner\" i], [class*=\"loader\" i]')",
            timeout=10000,
        )
    except Exception:
        pass

    btn = draft_button(page)
    if not btn:
        raise RuntimeError("'Save draft' button not found (run with --inspect).")
    btn.scroll_into_view_if_needed()
    pause(page, 0.7, 2)
    start_url = page.url
    btn.hover()
    pause(page, 0.2, 0.7)
    btn.click()

    # Success = we navigate away from the new-item form.
    try:
        page.wait_for_url(lambda u: u != start_url and "/items/new" not in u, timeout=20000)
    except Exception:
        raise RuntimeError("Clicked 'Save draft' but the page did not move on - check the Chrome window for a validation error.")


def load_done() -> set[str]:
    return set(json.loads(DONE_FILE.read_text())) if DONE_FILE.exists() else set()


def post_one(page, r: dict, args, done: set[str], label: str) -> bool:
    """Create one draft; record it in done.json. Returns True on success."""
    name, image = r["name"].strip(), r["image"]
    print(f"  {label} yugioh-{name} ... ", end="", flush=True)
    if not Path(image).exists():
        print("SKIP (image missing)")
        return False
    try:
        make_draft(page, args.site, image, name, description_for(r), args.price, args.condition)
    except Exception as e:
        print(f"FAILED: {e}")
        return False
    done.add(image)
    DONE_FILE.write_text(json.dumps(sorted(done), indent=1))
    print("saved as draft")
    return True


def human_wait(args) -> None:
    wait = random.uniform(args.delay, args.delay * 2.5)
    if random.random() < 0.15:  # now and then take a longer "coffee" break
        wait += random.uniform(30, 120)
    print(f"      (waiting {wait:.0f}s)")
    time.sleep(wait)


def post_all(args) -> None:
    rows = [r for r in read_csv() if r.get("name", "").strip()]
    done = load_done()
    todo = [r for r in rows if r["image"] not in done]
    if not todo:
        print("Nothing to post (all cards in names.csv are already done - delete done.json to redo).")
        return
    print(f"\nPosting {len(todo)} draft(s) to {args.site} ({len(rows) - len(todo)} already done).")

    pw, browser, ctx = connect(args.cdp)
    page = ctx.new_page()
    page.bring_to_front()
    failed = []
    try:
        for i, r in enumerate(todo, 1):
            if not post_one(page, r, args, done, f"[{i}/{len(todo)}]"):
                failed.append(r["name"].strip())
                if args.stop_on_error:
                    break
            if i < len(todo):
                human_wait(args)
    finally:
        page.close()
        pw.stop()  # disconnects only - your Chrome stays open

    print(f"\nDone. {len(todo) - len(failed)} saved, {len(failed)} failed.")
    if failed:
        print("Failed: " + ", ".join(failed) + "\nRerun the same command to retry only these.")


def watch(args) -> None:
    """Connect to Chrome ONCE, then keep drafting new photos as they land in the folder."""
    from engine import Engine, Settings
    eng = Engine(Settings(folder=args.folder, site=args.site, price=args.price,
                          condition=args.condition, delay=args.delay, cdp=args.cdp))
    print("Cards that need a check are held: fix them in names.csv and set check to 'ok'. Ctrl+C to stop.")
    eng.start()
    try:
        while eng.running():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Stopping...")
        eng.stop()
        eng.thread.join()


def inspect(args) -> None:
    pw, browser, ctx = connect(args.cdp)
    page = ctx.new_page()
    page.goto(f"{args.site}/items/new", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    fields = page.evaluate(
        """() => [...document.querySelectorAll('input,textarea,button,select')].map(e => ({
            tag: e.tagName, type: e.type, id: e.id, name: e.name, testid: e.dataset.testid,
            text: (e.innerText || e.placeholder || '').trim().slice(0, 60)}))"""
    )
    for f in fields:
        print(f)
    print(f"\nURL: {page.url}")
    page.close()
    pw.stop()


# ----------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="Turn Yu-Gi-Oh! card photos into Vinted drafts.")
    ap.add_argument("--folder", default=str(DEFAULT_CARDS), help="folder with card photos (default: ./cards)")
    ap.add_argument("--site", default="https://www.vinted.hr", help="your Vinted site, e.g. https://www.vinted.de")
    ap.add_argument("--price", default="1", help="price in EUR (default 1)")
    ap.add_argument("--condition", default="New without tags", help="condition label exactly as Vinted shows it")
    ap.add_argument("--cdp", default=None, help="override Chrome address (default: auto-detect your running Chrome)")
    ap.add_argument("--delay", type=float, default=15.0, help="min seconds between listings (random up to 2.5x, plus occasional longer breaks)")
    ap.add_argument("--yes", action="store_true", help="don't pause to review names.csv")
    ap.add_argument("--stop-on-error", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--extract-only", action="store_true")
    g.add_argument("--post-only", action="store_true")
    g.add_argument("--inspect", action="store_true", help="dump the Vinted form fields (for fixing selectors)")
    g.add_argument("--watch", action="store_true", help="stay connected and draft new photos as they appear (one 'Allow')")
    args = ap.parse_args()
    args.site = args.site.rstrip("/")

    if args.inspect:
        return inspect(args)
    if args.watch:
        return watch(args)
    if not args.post_only:
        folder = Path(args.folder)
        folder.mkdir(exist_ok=True)
        extract_all(folder)
        if args.extract_only:
            return
        if not args.yes:
            input("\nCheck/edit names.csv now (fix any wrong names, clear a name to skip that card).\n"
                  "Save it, then press Enter to start creating drafts... ")
    if not NAMES_CSV.exists():
        sys.exit("names.csv not found - run without --post-only first.")
    post_all(args)


if __name__ == "__main__":
    main()
