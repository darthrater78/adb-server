# Handoff: ADB Server (APK Pusher) — v3.7.0 workflow polish

**Goal:** v3.7.0 (branch `claude/dev-skills-workflow-hf1ddt`): the user found
the workflow clumsy and asked for every block to be collapsible. Built:
- **One push flow.** Install's Push opens the same CSS `:target` confirm
  dialog as Status (`templates/_push.html`, `confirm_dialog` macro, imported
  `with context`). The progress page (`/installs/<id>` or
  `/installs/batch?ids=…`) takes `back`, rebuilt server-side by
  `routes_install._safe_back` (only `/status` or `/install?to=<known serial>`),
  and meta-refreshes back with `?ok=Installed …` once everything succeeded.
- **Update all** (`POST /update-all`, device_serial): the device's Status
  updates, each through `pushes.create_install`, run one after another in one
  background task (`_run_pushes`). Shown when a device has 2+ updates.
- **Trust after pairing.** Pair redirects to `/devices?trust=<serial>`; the
  page offers Trust (with an optional nickname, `nickname` on the trust form)
  only for an existing, untrusted device.
- **Every block folds:** `<details class="fold">` with the heading in the
  summary and a CSS-drawn chevron (`style.css`, "Collapsible blocks").
  Settings pages with h3 subsections use `fold sub`.
- **Sources** dropped its uploads table (Install lists them); it shows a count.
- `web.redirect` now appends with `&` when the path already has a query.

**Current state:** 616 tests pass (`tests/test_workflow.py` is new). All
screenshots retaken at 3.7.0 by `scripts/screenshots/shoot.py` (it now opens
the folded "Watch a GitHub repo" form first).

**Gate status:** `.claude/dev-skills-gates.md`.

**Mode:** session 12 ran semi-autonomous on Opus 5.5 (user-approved). The
next session asks again.

**Lessons:**
- No Docker in the web container: the screenshot stack ran from the tree
  instead. Symlink `/app` → `app/`, run `mock_github.py` (port changed to a
  local one) with DB_PATH/STAGING_ROOT/ADB_INFO_DIR in the scratchpad, seed
  with `seed.py`, then `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python
  scripts/screenshots/shoot.py <url> <out> <password> base|mfa`.
- The test venv goes in the scratchpad: `python3 -m venv <dir> && <dir>/bin/pip
  install -r requirements-dev.txt`, then `PATH=<dir>/bin:$PATH bash scripts/test.sh`.

**Also open:** Dependabot PR #2 (python 3.14 app base image).
