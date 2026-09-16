# Dev Skills gate state
Track: work commit — CI/CD scaffolding, no version bump
Version: 0.2.0 (VERSION)
Updated: 2026-09-16

Env: remote container | Repo: darthrater78/adb-server
Branch: claude/dev-skills-currency-iiqxta (local only — not on remote)
Default branch: feature/apk-pusher-scaffold
Skill: dev-skills v2.22.0 — current (latest upstream tag v2.22.0)

🔢 VERSION    ⬜
🔨 BUILD      ⬜ (image build unverifiable here — no docker daemon in this container)
🔒 SECURITY   ✅ 0 Critical, 0 High — pip-audit clean; workflow security checklist applied
📄 DOCS       ⬜
📦 RELEASE    ⬜
🚀 SHIP       ⬜

## Open findings (session start)
- Unfinished Gate 6: v0.1.0 and v0.2.0 have CHANGELOG entries and are on the
  default branch, but `git ls-remote --tags origin` returns NO tags at all.
  Neither release was ever tagged or published.
- No CI: `.github/workflows/` does not exist — no build check, no release
  workflow. Gate 5 PRs are unvalidated; Gate 6 has no publish path.

## Changed this session (uncommitted)
- .github/workflows/ci.yml       (new)
- .github/workflows/release.yml  (new)
- .github/workflows/lint-workflows.yml (new)
- .github/dependabot.yml         (new)

Verified locally: actionlint 1.7.12 clean on all three workflows; dependabot.yml
parses; VERSION/CHANGELOG check passes; release-notes awk extracts 17 lines for
v0.2.0; `docker compose config` valid; pip-audit reports no known
vulnerabilities. NOT verified: the two image builds — this container has the
docker CLI but no daemon.
