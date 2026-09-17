# Dev Skills gate state
Track: work commit — manual APK upload + debug-build policy, no version bump
Version: 0.2.0 (VERSION)
Updated: 2026-09-17 (feature)

Env: remote container | Repo: darthrater78/adb-server
Branch: claude/dev-skills-currency-iiqxta (pushed, tracking origin)
Default branch: feature/apk-pusher-scaffold
Skill: dev-skills v2.22.0 — current (latest upstream tag v2.22.0)

🔢 VERSION    ⬜ not owed on a work commit
🔨 BUILD      ⬜ image build still unverifiable here (docker CLI, no daemon) — CI covers it
🔒 SECURITY   ✅ re-run on this diff: pip-audit clean, all 8 deps at current release
📄 DOCS       ⬜ README quickstart updated; no CHANGELOG entry yet (no version bump)
📦 RELEASE    ⬜
🚀 SHIP       ⬜

## Open findings (session start)
- Unfinished Gate 6: v0.1.0 and v0.2.0 have CHANGELOG entries and are on the
  default branch, but `git ls-remote --tags origin` returns NO tags at all.
  Neither release was ever tagged or published. Note release.yml now requires
  a passing CI run on the tagged commit, which cannot exist for commits that
  predate CI — tag a commit that CI has run on.

## Audit (2026-09-17): 2 High, 4 Medium, 7 Low
Fixed 12 of 13. Deferred by the user: COOKIE_SECURE/HTTPS default — to be
resolved by putting ACME/Let's Encrypt in front rather than by relaxing the
cookie flag.

## Changed this session (uncommitted) — manual APK upload, debug builds allowed on upload
12 files, +408/-56. Audit fixes already committed as 3426398 and pushed.

Debug policy (user decision): debug-signed APKs are accepted on the MANUAL
UPLOAD path only, flagged is_debug and warned about. A polled release that is
debug-signed is still refused — nothing is watching when the poller runs, and
a refused release does not set the repo's pin. apk_verify reports the flag
(SignerInfo) instead of enforcing it, so the two paths differ on purpose.

Verified locally:
- Migration replayed against a database built to the exact v0.2.0 schema:
  repo_id nullable, source and is_debug added (default 0), rows survive with
  ids and release notes, install row intact, foreign_key_check clean, all 7
  indexes restored after the rebuild, second init_db() a no-op.
- Upload end to end: CSRF enforced; non-APK and oversized refused; debug-signed
  upload accepted, marked is_debug=1, Debug badge and warning flash rendered;
  valid upload stored as <sha256>.apk with the client filename kept only as a
  label ("../../etc/evil.apk" -> "evil.apk"); no temp files left; identical
  re-upload refused; uploaded APK pushes to a trusted device and appears on
  /installs through the LEFT JOIN.
- Debug policy on the polled path: debug-signed release refused, surfaced on
  /repos, pin not written, no temp file left; a release-signed one from the
  same repo then stages and sets the pin.
- Regression: all audit-fix checks and the app smoke test still pass.
- actionlint, compileall, compose config, pip-audit all clean.
NOT verified: the two image builds (no docker daemon in this container).
