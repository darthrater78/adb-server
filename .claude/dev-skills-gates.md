# Dev Skills gate state
Track: release sequence → v1.0.0 on main
Mode: semi-autonomous (approved 2026-09-23, session 2) — commits and the tag still require the user's approval
Origin: darthrater78/adb-server (not a fork)
Version: 1.0.0
Updated: 2026-09-23 (session 2)

🔢 VERSION    ✅ 1.0.0 everywhere (VERSION, CHANGELOG, UI via app_version; hardcoded preview pill fixed)
              header links: repo + releases/tag/v1.0.0 (added at user's request; test_web covers it)
              prior tags on origin (user-pushed 2026-09-23): v0.1.0→3c600ae, v0.2.0→03c2235
🔨 BUILD      ✅ 2026-09-23 s2 — all 3 images built (adbshots stack), app + adb-server healthy,
              `adb devices` inside app OK, apksigner 0.9 / aapt present, mdns image imports
              zeroconf 0.151.3; 319 pytest passed; header re-checked at 1280/1100/390 (no overflow); actionlint 1.7.12 clean (no local shellcheck)
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              Full top-to-bottom review 2026-09-23 s2 (all app/, mdns/, Dockerfiles, compose, workflows).
              deps: pip-audit clean (app+mdns+dev), all pins latest, Dependabot 0 open alerts.
              bandit: only withdrawn B404/B603/B607/B608.
              ✅ fixed L: TOTP check race — parallel requests could reuse a code / exceed 5 guesses
                 → attempt counted first, step claimed atomically (db.increment_meta/advance_meta)
              ✅ fixed L: code re-pair adopted a legacy ip:port row incl. trust → trust cleared
                 (user chose "keep all but trust", 2026-09-23); test that encoded it updated
              ✅ fixed L: push via legacy row could install on another registered (untrusted) phone
                 on the same IP → is_same_device refuses when the serial has its own row
              ✅ fixed L: run_push didn't re-check trust at install time → re-checked
              ✅ fixed L: mdns table full (256) refused new announcements → evicts oldest
              ✅ fixed (env): dev-server .env was 0664 → 0600; README/.env.example say chmod 600
              All 7 new/changed regression tests fail against pre-fix code, pass with it.
              ↩ withdrawn: mDNS spoofing of the QR service name — adb pair is SPAKE2 with a 16-char
                 password, so a spoofer only causes a failed pairing (DoS on the LAN)
              ↩ withdrawn: global MFA lockout DoS — recorded design decision; recovery path exists
              Accepted by user earlier (documented, not findings): plain HTTP on LAN, no at-rest
                 encryption of TOTP secret / notify URLs
              Previous ✅ items (2026-09-23 s1) remain fixed.
📄 DOCS       ✅ CHANGELOG 1.0.0 (+ today's security fixes); README rewritten: 3 containers, every
              feature, detailed Security section, 15 mock-data screenshots; .env.example fixed
              (ALLOWED_ORIGIN, COOKIE_SECURE); HANDOFF updated
📦 RELEASE    ✅ commits approved by user 2026-09-23 — 2 commits → PR feat/merge-17th-work → main
🚀 SHIP       ⏳ plan: CI green → Apprise-review HOLD (user) → merge → user pushes v1.0.0 tag → verify

Notes:
  Working branch: feat/merge-17th-work; NOT committed. Everything is staged in the index
    (re-staged at handoff). Full context in HANDOFF.md.
  Next: tags (user) → re-present approval → release sequence in HANDOFF.md
    (tags → re-present approval → commits → PR → Apprise-review hold → merge → v1.0.0 tag).
