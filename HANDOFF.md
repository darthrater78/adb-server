# Handoff: adb-server (APK Pusher)

**Goal:** Self-hosted system that watches GitHub repos for new APK releases,
verifies them, and pushes them to wireless-ADB-paired Android devices.

**Current state:** `VERSION` still reads 0.2.0. Work for the next release sits
under `## Unreleased` in `CHANGELOG.md`, on branch
`claude/dev-skills-currency-iiqxta`, four commits pushed to origin and all CI
green:

```
89cd290  CI, release, workflow-lint and Dependabot configuration
3426398  12 security/quality audit findings fixed
1528ad9  Manual APK upload + debug-build policy
c7442fc  shellcheck SC2016 fix (clears the last red)
```

The 0.2.0 stack is deployed and live-tested end-to-end, including a real push
to a real paired device ("Dad"), at `http://10.0.0.252:8080` (LAN) and
`http://127.0.0.1:8080`. That deployment predates everything on this branch —
it has not been rebuilt against the branch.

Since 0.2.0 this branch added:

- **CI/CD, where there was no `.github/` at all.** `ci.yml` (byte-compile,
  VERSION↔CHANGELOG agreement, compose validation, `pip-audit`, and a matrix
  build of both Dockerfiles), `release.yml` (tag-triggered, publishes both
  images to GHCR and creates the release), `lint-workflows.yml` (actionlint),
  and `dependabot.yml` (github-actions, pip, docker).
- **12 audit findings fixed** — two High, four Medium, seven Low. The two that
  matter most: the upstream release tag was being interpolated into a
  filesystem path (a tag containing `/`, which git permits, stopped the poll
  loop for every repo after it, every cycle), and APK verification only ever
  inspected the first signer.
- **Manual APK upload.** Stage a build from the Staged page without it being a
  GitHub release. Content-addressed storage, same verification as a polled
  release, no repo and therefore no interaction with any repo's pin.
- **Debug builds allowed on upload only**, flagged `is_debug`, badged, and
  warned about. The poller still refuses them.

**Gate status (dev-skills, `.claude/dev-skills-gates.md`):**

```
🔢 VERSION    ⬜ 0.2.0 unchanged — this branch is work commits, not a release
🔨 BUILD      ✅ both images build in CI on every code commit (run 1-3 green)
🔒 SECURITY   ✅ pip-audit clean; all 8 deps pinned AND at current release
📄 DOCS       ⬜ CHANGELOG entry sits under "## Unreleased"; README current
📦 RELEASE    ⬜ no PR opened yet
🚀 SHIP       🚫 v0.1.0 and v0.2.0 were never tagged — see "Open items"
```

**Key files:**

- `app/apk_verify.py::SignerInfo` — verification *reports* the debug
  certificate rather than raising on it, so the poller and the upload endpoint
  can apply different policy on purpose. Multi-signer APKs are checked and
  pinned across every signer; a single-signer APK still yields one bare
  fingerprint, so pins written by 0.2.0 keep matching.
- `app/main.py::upload_apk` — the upload endpoint. Sync on purpose (Starlette
  threadpool, keeps apksigner off the poller's event loop). The client filename
  is display text only; the path is always `uploads/<sha256>.apk`.
- `app/db.py::_migrate_staged_apks_shape` — SQLite cannot drop a NOT NULL in
  place, so `staged_apks` is rebuilt to make `repo_id` nullable. Own autocommit
  connection (PRAGMA foreign_keys cannot change inside a transaction), explicit
  row-count check before the old table is dropped. Runs *before* `SCHEMA`, or
  the rebuild would take the indexes with it.
- `app/poller.py::check_repo` — `tempfile.mkstemp`, never the tag, builds the
  staging path. `poll_all_repos` gives each repo its own handler.
- `.github/workflows/release.yml` — the `gate` job refuses to publish unless
  CI has actually *passed* on the tagged commit, polled via the Actions API.
  `:latest` only moves when the tag is the newest stable version.
- `.github/workflows/lint-workflows.yml` — **actionlint silently skips shell
  linting when shellcheck is absent.** A local run can report clean on a
  workflow that fails in CI. Install shellcheck before trusting one.

**Decisions made:**

- **Debug-signed APKs: upload path only.** An operator is present to read the
  warning; nothing is watching when the poller runs. A refused polled release
  does not write the repo's pin — the default Android debug certificate is
  generated locally per machine, so pinning one pins something anybody can
  reproduce.
- **An upload never touches a repo's pin.** It has no repo to pin against, and
  letting an operator-supplied file write one would undermine what the pinning
  exists for.
- **`staged_apks.repo_id` is nullable rather than pointing at a synthetic
  "manual uploads" pseudo-repo.** The pseudo-repo avoids a migration but leaves
  a fake repo the poller and the UI must each remember to skip. Every listing
  join is now a LEFT JOIN — including the installs query, which would otherwise
  silently drop every install of an uploaded APK.
- **HTTPS is deferred to a reverse proxy with ACME/Let's Encrypt**, not to
  relaxing `COOKIE_SECURE`. Until then `COOKIE_SECURE=true` (the default) and
  plain-HTTP LAN access are in conflict: a browser will not store a `Secure`
  cookie over `http://` except on localhost, so login silently fails. This is
  the one audit finding deliberately left open.
- **CSP stays `script-src 'none'`** — every feature so far, progress bar and
  theme switcher included, was built without client-side JS to preserve it.
- **Single-worker uvicorn is required** (APScheduler runs in-process). Sync
  route handlers rely on Starlette's threadpool, so they do not reintroduce a
  blocking-call regression.
- Base images are pinned by digest, not tag; Dependabot bumps them.

**Open items, in the order they should be done:**

1. **No PR.** Nothing merges without one. Base branch is
   `feature/apk-pusher-scaffold`, which is also the repo's default branch —
   there is no `main`, and that is worth renaming before it confuses something.
2. **v0.1.0 and v0.2.0 were never tagged.** Both are on the default branch with
   changelog entries and no tag or release behind them — two unfinished Gate 6s
   predating this branch. `release.yml` will refuse to publish a commit CI never
   ran on, which is true of every commit older than 89cd290.
3. **Cut 0.3.0** — bump `VERSION`, give `## Unreleased` a real heading and date,
   then tag. Tag the version-bump commit specifically: it touches `VERSION` and
   `CHANGELOG.md`, so CI runs on it and the release gate is satisfied. A
   workflows-only commit is skipped by `ci.yml`'s `paths-ignore` and would fail
   that gate.
4. **ACME/TLS**, which retires the `COOKIE_SECURE` conflict above.

**Shell environment:** Linux Terminal (bash/zsh). Note that this branch's work
was done in a remote container, where git is executed directly rather than
handed over as commands — but **tag pushes are always run by hand**, so step 3
above ends with a block to paste locally.

**Next step:** Open the PR into `feature/apk-pusher-scaffold` (item 1). If
picking this up cold: read `.claude/dev-skills-gates.md` for live gate state and
run `git log --oneline` and `git status` first — this file goes stale the moment
more work happens.
