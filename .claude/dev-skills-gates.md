# Dev Skills gate state
Track: work commit
Mode: semi-autonomous (approved 2026-09-23) — commits and the tag still require the user's approval
Origin: darthrater78/adb-server (not a fork)
Version: 0.2.0 (unreleased work on top; user staying in dev, no tags)
Updated: 2026-09-23

🔢 VERSION    ⬜ not owed (work commit)
🔨 BUILD      ⬜ not owed (work commit)
  docker info: daemon unreachable in this container; pytest 101 passed; actionlint clean; CI run 35813565727 green (tests + both image builds)
🔒 SECURITY   ✅ 1 open — 0 Critical, 0 High (work commit OK; blocks release)
  pip-audit: no known vulnerabilities; all pins at latest release
  ✅ fixed: F1 rejected-release redownload loop (Medium)
  ✅ fixed: F2 blocking apksigner/aapt on event loop (Medium)
  ✅ fixed: F3 orphaned staging files on repo delete (Low)
  ✅ fixed: F4 tag with "/" crashed poll of all later repos (Medium)
  ✅ fixed: unbounded _failed_attempts dict in auth.py (Low)
  ✅ fixed: no .github/dependabot.yml → added (actions, pip x2, docker)
  📝 open: Dependabot alerts setting unverified (Medium) — no credential reaches the endpoint
  ✅ fixed: base images pinned by digest (Dependabot docker keeps them current)
📄 DOCS       ⬜ not owed (work commit)
📦 RELEASE    ⬜ not owed (work commit)
🚀 SHIP       ⬜ not owed (work commit)
