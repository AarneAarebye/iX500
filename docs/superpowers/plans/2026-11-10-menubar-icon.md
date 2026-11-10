# scanix500 menu bar icon (draft C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Replace the emoji 📄 / 📄… in the Mac menu bar app with proper template icons (draft C "the page and beam", chosen by the user on 2026-11-10): idle = plain page, scanning = page with two chevrons flowing in; a third icon, collecting = stack of sheets, ships now but is wired only when the Mac's multi-page collecting exists.

**Architecture:** `scripts/make_menubar_icons.py` (PIL, supersampled) draws three 36×36 px PNGs (144 dpi, so macOS shows them 18 pt tall) into `src/scanix500/menubar/icons/`; the committed PNGs are the product (hatchling's wheel includes everything under `src/scanix500`). `menubar/app.py` shows them as template images (macOS tints them for light/dark) and falls back to the emoji if a file is missing.

**Tech Stack:** Python 3.11, rumps (`icon=`, `template=True`), Pillow, pytest (`.venv/bin/python -m pytest`).

## Global Constraints

- Template icons: black on transparent, alpha only; no colour. 36×36 px, saved with `dpi=(144, 144)`.
- Names: `idle.png`, `scanning.png`, `collecting.png` in `src/scanix500/menubar/icons/`.
- The emoji titles stay as the fallback (`IDLE_TITLE`, `SCANNING_TITLE`) when an icon file is missing or cannot be set; the title is empty while an icon is shown.
- Nothing slow on the main thread; setting `self.icon` happens where `self.title` is set today (`_start_scan` and `_on_scan_complete`).
- Version 0.10.3 (`pyproject.toml`, `src/scanix500/__init__.py`, version strings in `tests/menubar/test_bridge.py`, README install example). README/AGENTS.md mention the icons and the generator.
- Commit messages end with: Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com> and Claude-Session: https://claude.ai/code/session_0117epTz6pLGF3vCWxi4JMsL

---

### Task 1: Icons, generator, app wiring

**Files:**
- Create: `scripts/make_menubar_icons.py`, `src/scanix500/menubar/icons/{idle,scanning,collecting}.png`, `tests/menubar/test_icons.py`
- Modify: `src/scanix500/menubar/app.py`, `tests/menubar/test_app.py`, version files, `README.md`, `AGENTS.md`

**Interfaces:**
- Produces: `scanix500.menubar.app.ICON_DIR` (a `Path`), `icon_path(name) -> str | None` (None if the file is missing), the app shows `idle` and `scanning`.

- [ ] **Step 1:** Port the drawing code of the approved draft: read `/tmp/claude-501/icons/drafts-final.py` (functions `c_idle`, `c_scan`, `c_count` and the helpers `page`, `canvas`, `u`; constants `S = 8` supersample, `W = 36`) into `scripts/make_menubar_icons.py`, which writes the three PNGs (downsample with LANCZOS, `save(..., dpi=(144, 144))`) and prints the paths. Keep the drawing identical to the draft (the user approved it); drop everything about the comparison sheet and drafts A and B. If `/tmp/claude-501/icons/drafts-final.py` no longer exists, recreate the drawing from this description: plain page outline with folded corner (9,5)–(27,31), 2.2 px line; scanning = the same page plus two downward chevrons at y 14–18 and 21–25 (x 14–22, line 2.4); collecting = three sheets: front page (6,8)–(24,33) with two text lines at y 18 and 23, plus two back sheets of which only the top and right edge show, offset 3.2 px each.
- [ ] **Step 2: Failing tests** in `tests/menubar/test_icons.py`: all three PNGs exist, are 36×36 RGBA, contain only black pixels (R=G=B=0) with some transparent and some opaque pixels, report dpi ≈ 144; `idle` and `scanning` differ; running the generator into a tmp dir (give it a `main(out_dir)` function) reproduces byte-identical files to the committed ones. In `tests/menubar/test_app.py` (follow its `ScanixMenuBarApp.__new__` style): starting a scan sets the scanning icon and an empty title, completing sets the idle icon; with a missing icon file the emoji titles are used (monkeypatch `icon_path` to return None).
- [ ] **Step 3: Implement.** `app.py`: `ICON_DIR = Path(__file__).parent / "icons"`; `icon_path(name)`; in `__init__` pass `icon=icon_path("idle"), template=True` to `super().__init__` and `title=None if icon else IDLE_TITLE` — **check rumps' source in the venv** (`.venv/lib/python*/site-packages/rumps/rumps.py`) to see how `title`, `icon` and `template` interact (rumps sets the status item's image/title in `setStatusBarIcon`/`setStatusBarTitle`; make sure an empty title does not fall back to the app name). Setting states: a helper `_show(state)` that sets `self.icon`/`self.title` (icon present → `self.icon = path; self.title = ""`, else `self.icon = None; self.title = emoji`). Replace the two `self.title = …` places. rumps requires `template=True` to be set on the app for `self.icon` assignments to stay templates — verify and handle.
- [ ] **Step 4:** Run the generator, commit the PNGs, run the full suite `.venv/bin/python -m pytest -q`. Bump version to 0.10.3; README/AGENTS.md short notes.
- [ ] **Step 5: Commit** `feat: menu bar icons (page, scanning, collecting) replace the emoji; 0.10.3`.
