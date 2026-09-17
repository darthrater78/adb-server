# Dev Skills gate state
Track: work commit — audit fixes, no version bump
Version: 0.2.0 (VERSION)
Updated: 2026-09-17

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

## Changed this session (uncommitted)
12 files, +172/-34 — see `git diff`.

Verified locally: actionlint clean; compileall clean; docker compose config
valid; pip-audit no known vulnerabilities; apk_verify multi-signer/debug-cert/
digest-ordering checks pass; redirect allowlist accepts GitHub hosts and
refuses look-alikes; rate-limit store evicts to its ceiling; app boots under
TestClient with login, CSRF, cross-origin-login and all pages exercised; the
six new indexes exist and the query planner uses them.
NOT verified: the two image builds (no docker daemon in this container).
