# Dev Skills gate state
Track: release sequence → v3.3.0 (source verification, artifacts, encryption, UI rework)
Mode: semi-autonomous (approved 2026-09-24, session 8) — commits and the tag still require the user's approval
Model: Opus 5.5 approved by user (session 8, current task)
Origin: darthrater78/adb-server (not a fork)
Standards: at-rest ✅ encrypted (secretbox: TOTP, notify URLs, GitHub token, signing-key passwords) · TOTP ✅ · rescue ✅ · Apprise ✅ · compose ✅ §10.5
Version: 3.3.0
Updated: 2026-09-24 (session 8: full audit, all findings fixed on this branch)

## Current: v3.3.0 (feat/repo-legitimacy) — source verification, workflow artifacts, secrets encrypted at rest
🔢 VERSION    ✅ 3.3.0 MINOR (user approved the commit carrying it: "commit here", 2026-09-24)
              VERSION, compose.yaml images + comment, README links/block all 3.3.0; prior tag v3.2.0 → 1ddd87f
🔨 BUILD      ✅ working tree (session 8 fixes): 520 pytest passed, tree unchanged during run; handoff offered
              images audit-wip built from the tree (hash-checked install; pip freeze == lockfile; trixie both;
              adb/aapt2/zipalign/apksigner/keytool run). Stack t33a on 10.0.0.252:18184 (gh token env only):
              all 14 pages 200; unsigned upload refused, then staged with opt-in (uploads key); real repo
              review/confirm + opt-in; repo key made with real tools: 2 keys, different certs, 600/700;
              Settings lists both by name; spoofed X-Forwarded-For ignored (audit = real IP, 429 after 5);
              FORWARDED_ALLOW_IPS=proxy → per-client limit + real IPs in audit; app → adb-server OK
              screenshots retaken with browser on bridge network (no host net); security + builds updated
              session 7 evidence (source verification, artifacts, Builds, signing badges): commit 23d3020
  test artifact: ⬜ rebuild from the exact PR head commit before merge
  test creds: generated per run, shown to user
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High (session 8 full audit: 12 found, 12 fixed, user: "fix it on this branch")
              ✅ fixed: 📝 M1 shared signing key → one key per source (github-<id> / uploads); tested with real tools
              ✅ fixed: ⚠️ H1 found while fixing: screenshots/run.sh ran Playwright with --network host (user rule:
                 never) → bridge network; retaken screenshots prove it works
              ✅ fixed: 💡 L1 rate limit: IPv6 by /64; FORWARDED_ALLOW_IPS documented + verified (spoof ignored)
              ✅ fixed: 💡 L2 short SECRET_KEY/APP_PASSWORD warned (log + banner), user chose warn over refuse
              ✅ fixed: 💡 L3 audit precedence (regression test fails without fix)
              ✅ fixed: 💡 L4 all stages on pinned trixie-slim · L5 hashed lockfiles, --require-hashes, CI agreement check
              ✅ fixed: 💡 L6 .env.example · L7 Playwright image pinned by digest (found with H1)
              ✅ fixed: 💡 Q1 main.py → web/uploads/routes_* (same 73 routes, verified) · Q2 shared sha256_file,
                 public identity_problem · W1 bandit in CI (-ll; B608 exceptions nosec'd with reasons)
              evidence: pip-audit --require-hashes clean; bandit -ll clean; pyflakes clean; actionlint ok; 520 passed
              Standards: at-rest ✅ encrypted (TOTP, notify URLs, GitHub token, per-source signing-key passwords)
📄 DOCS       ✅ session 8: CHANGELOG 3.3.0 + audit fixes; README (per-source keys, FORWARDED_ALLOW_IPS, rate limit,
              short secrets, lockfiles/bandit, data at rest; stale "no encryption at rest" + NOTIFY_EVENTS fixed);
              .env.example; HANDOFF layout; settings-security.png + builds.png retaken
              session 7: CHANGELOG 3.3.0 (+ Settings split, token template/expiry/access); README Settings + GitHub token sections,
              Sources (review, artifacts), Source verification section, threat model row,
              Data at rest (encrypted + SECRET_KEY change), GitHub access, Configuration; .env.example; HANDOFF
              pre-existing inaccuracies fixed: ETag 304s only free when authenticated; Apprise guide said "unencrypted"
              ALL 16 screenshots retaken from invented data, GitHub mocked (scripts/screenshots/run.sh): no real
              repo/account/token/device in any; new repo-review.png + builds.png; README refs all resolve, none unused
📦 RELEASE    ⏳ commit + release notes approved (user: "yes, commit and ship it", 2026-09-24); notes = CHANGELOG 3.3.0
              branch synced (0 behind origin/main); next: commit, push, PR, PR-head test images, merge on green CI
🚀 SHIP       ⬜

## Previous: v3.2.0 (feat/zip-apk-upload) — upload an artifact zip holding one APK; aapt → aapt2
🔢 VERSION    ✅ 3.2.0 MINOR (user: "2" = release as v3.2.0); VERSION, compose.yaml images + comment,
              README links/compose block all 3.2.0; prior tag v3.1.0 → e9da85a on origin
🔨 BUILD      ✅ final tree: 337 pytest passed (py3.13), tree unchanged during run
              app image from working tree (aapt2: ldd resolves, 2.20-15087165), throwaway zipg2-app, gen creds:
              artifact.zip staged "from artifact.zip"; bare dup refused; unsigned-in-zip, 2-APK, no-APK refused;
              blind.zip (was aapt failure) staged; staged dir + spool clean after each; UI v3.2.0. Removed
  test artifact: adb-server-test/app:1888198 (22877548f40f), adb-server-test/adb-server:1888198 (4c6f871c74b9)
              @ 1888198 (PR #12 head, git archive): 2-container stack healthy, sign-in, 7 pages 200,
              real zipped APK staged, app → adb-server `adb devices` OK, UI v3.2.0; merge 1ddd87f tree identical
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              zip: never extracted by name, one APK only, no recursion, 1000-entry cap, declared + metered
              inflate cap, encrypted refused, zip deleted by os.replace before APK checks
              ✅ fixed: 💡 Low — corrupt deflate stream → 500; regression test fails without fix (verified)
              ✅ fixed: 📝 quality (pre-existing) — legacy aapt failed on current-SDK APKs → aapt2 (same r37 zip)
              bandit: only the 6 known B608 in db.py (unchanged) — withdrawn as before; pip-audit --strict clean
📄 DOCS       ✅ CHANGELOG 3.2.0; README Sources upload + Manual uploads (zip rules), aapt2 refs; form hint
              sources.png screenshot predates the one-sentence hint change (not a behavior claim)
📦 RELEASE    ✅ commit + release notes approved (user: "yes", 2026-09-24); notes = CHANGELOG 3.2.0
              branch synced (0 behind origin/main); PR opened from this commit
🚀 SHIP       ✅ v3.2.0 shipped 2026-09-24 — PR #12 MERGED → 1ddd87f; CI success on merge commit
              tag v3.2.0 → 1ddd87f (user-driven, ls-remote); release run success; release published, Latest
              ghcr app + adb-server :3.2.0 pullable; app:latest = 3.2.0 digest; image runs aapt2, VERSION 3.2.0
              branch feat/zip-apk-upload deleted (user-driven, ls-remote empty)
