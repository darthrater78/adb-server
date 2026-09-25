# Handoff: ADB Server (APK Pusher) — after v3.7.1

**Goal:** v3.7.1 is shipped; nothing is in flight. The next session starts
fresh work from `main`.

**Current state:**
- `main` at 199d280 (merge of PR #18). Tag `v3.7.1` → 199d280; release run
  36189810427 succeeded; GitHub release v3.7.1 is Latest; ghcr
  `adb-server/app` and `adb-server/adb-server` `:3.7.1` = `:3.7` = `:latest`.
- v3.7.1 fixed Builds (opens on the newest release and commit, older ones
  fold) and dev builds reporting their full version (`release.yml` stamps
  `3.7.1-dev.N` into VERSION for `-suffix` tags).
- 630 tests pass (`scripts/test.sh`).
- No test containers left running.

**Gate status:** all six ✅ for v3.7.1 (`.claude/dev-skills-gates.md`).

**Mode:** session 15 ran semi-autonomous on Opus 5.5 (user-approved), shell
Linux bash, enforcement kept on. The next session asks again.

**Key files:**
- `.github/workflows/release.yml` — full tags publish `:X.Y.Z`/`:X.Y`/`:latest`
  and a GitHub release; `-suffix` tags publish only `:<full version>`.
- `compose.yaml` — image tags pinned to the version (Gate 1 bumps them).

**Decisions made:**
- The user pushes tags and deletes refs; Claude verifies them afterwards.
- The user's data lives in `/opt/docker/adb-server`, compose in
  `/opt/docker/stacks/adb-server`.

**Open:**
- `.claude/dev-skills-gates.md` is tracked but gitignored — untrack it
  (`git rm --cached`) this commit; other clones drop their copy on next pull.
- Remote branches to review for deletion (user runs deletions):
  `claude/dev-skills-workflow-hf1ddt` (merged in #17), `feat/simpler-flow`
  (not checked).
- Dependabot PR #2: python 3.13-slim → 3.14-slim for `/app`.

**Lessons:**
- `git checkout main` reset the tracked gate file, dropping session edits;
  untracking it fixes that.
- Test venv goes in the scratchpad: `python3 -m venv <dir> && <dir>/bin/pip
  install -r requirements-dev.txt`, then `PATH=<dir>/bin:$PATH bash scripts/test.sh`.

**Shell environment:** Linux bash on the server (local session, same clone).

**Next step:** ask the user what to build next (or triage Dependabot PR #2).
