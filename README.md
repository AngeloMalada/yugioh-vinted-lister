# Yu-Gi-Oh! → Vinted drafts

## Desktop app
Install with `installer\YugiohVintedLister-Setup.exe`. It needs no admin rights and adds a Start-menu entry.
1. Drag card photos onto the window, or click **Add photos**.
2. Check any cards marked **Needs review**: click **Edit**, fix the name, which autocompletes from the official card list, pick the set, then **Save & approve**.
3. Click **Start listing**, then **Allow** in Chrome. Drafts are created one by one at a human pace.

The app keeps its data in `%APPDATA%\YugiohVintedLister`. By default photos are copied into `Pictures\Yugioh cards`; change this under **Photos folder**.
To rebuild after code changes, run `powershell -File build.ps1`.

## Publishing an update
```
powershell -File release.ps1 1.1.0
```
This creates and pushes the branch `release/1.1.0` from your current commit. You can also create that branch yourself on GitHub. Creating a `release/<version>` branch is the only thing that triggers a build. GitHub Actions builds the installer and publishes release `v1.1.0`, which takes about 3 minutes. Pushing to `main` or other branches never builds. Later pushes to an existing release branch don't rebuild either: release the fix as the next version, for example `release/1.1.1`.
Installed apps check GitHub at startup and every 6 hours. They show an **Update available** banner, and **Update & restart** installs the new version silently and reopens the app. Your data in `%APPDATA%` is kept.

## Command line

## One-time setup
The script uses your normal, already-open Chrome, where you're already logged in to Vinted.
1. In Chrome, open `chrome://inspect/#remote-debugging`.
2. Turn on **Allow remote debugging for this browser instance**.
3. When the script connects, Chrome asks for permission. Click **Allow**.

(If the venv is missing: `python -m venv .venv` then `.venv\Scripts\pip install -r requirements.txt`.)

## Each time
1. Put card photos in `cards\` (one card per photo; jpg/png/webp).
2. Make sure your Chrome is open.
3. Run:
   ```
   .venv\Scripts\python vinted_lister.py
   ```
4. It prints the name it found for each card and writes them to `names.csv`. Anything under 80% is marked `CHECK THIS`.
   Fix names in `names.csv` if needed. Clear a name to skip that card. Then press Enter.
5. For each card it opens Vinted's "sell" form, uploads the photo, and fills in:
   - title `yugioh-<name>`
   - description `<name>`, then on the next line `<set name> (<set code>)`. The set comes from the code printed under the artwork, e.g. MP25-EN201.
   - price `1`

   Then it clicks **Save draft** and moves to the next card.

Finished cards are recorded in `done.json`, so rerunning only retries the ones that failed.

## Watch mode (one "Allow" per session)
```
.venv\Scripts\python vinted_lister.py --watch
```
Click **Allow** once. The script stays connected and turns every photo you drop into `cards\` into a draft, one by one, with human-like pauses.
- Cards whose check passed are drafted automatically.
- Flagged cards are marked **HELD**. Fix the name in `names.csv`, change its `check` to `ok` and save. The script picks it up within a few seconds.
- Press Ctrl+C to stop. Your Chrome stays open.

## Options
| flag | meaning |
|---|---|
| `--site https://www.vinted.de` | your Vinted country (default vinted.hr) |
| `--price 2` | different price |
| `--extract-only` / `--post-only` | run just one half |
| `--yes` | skip the review pause |
| `--inspect` | print the Vinted form's fields (use if Vinted changes its page and a field isn't found) |
