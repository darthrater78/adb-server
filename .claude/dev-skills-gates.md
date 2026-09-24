# Dev Skills gate state
Track: release sequence → v3.3.0 (source verification, artifacts, encryption, UI rework)
Mode: semi-autonomous (approved 2026-09-24, session 7) — commits and the tag still require the user's approval
Model: Opus 5.5 approved by user (session 7: zip upload, repo hardening)
Origin: darthrater78/adb-server (not a fork)
Standards: at-rest ✅ encrypted (secretbox: TOTP, notify URLs, GitHub token) · TOTP ✅ · rescue ✅ · Apprise ✅ · compose ✅ §10.5
Version: 3.2.0
Updated: 2026-09-24 (session 7, handoff written)

## Current: v3.3.0 (feat/repo-legitimacy) — source verification, workflow artifacts, secrets encrypted at rest
🔢 VERSION    ✅ 3.3.0 MINOR (user approved the commit carrying it: "commit here", 2026-09-24)
              VERSION, compose.yaml images + comment, README links/block all 3.3.0; prior tag v3.2.0 → 1ddd87f
🔨 BUILD      ✅ working tree: 498 pytest passed (py3.13), tree unchanged during run
              stack t330 on 10.0.0.252:18183 (gh token in env only, tmpfs /data), rebuilt after Settings split:
              real review/confirm of android-heartrate + hunter-douglas-blind (IDs pinned); wrong-id confirm refused;
              real artifact staged w/ provenance, no zip/spool left; real poll staged v1.5.1 (github-actions[bot]);
              Settings token saved sealed (raw absent from db), bad token refused; GitHub access table OK/OK for both;
              every Settings page + artifacts desk/phone dark: no horizontal overflow
              Install: Releases / Test builds / Uploads sections, artifact card badged + branch/commit→run (screenshot)
              device names: nickname > model · …serial tail > serial (unit tested; phone re-pair pending)
              real: dockge + cert-generator refused (no APK; cert-generator via artifact zip-list range read),
              hunter-douglas-blind accepted artifact-only (Azure: explicit ranges, suffix ranges ignored → fixed);
              Builds: 6 releases, v1.5.0 staged via release checks, latest stayed v1.5.1; 25 artifacts in 15 commit
              groups with messages, release builds excluded; unsigned heartrate APK refused, then with opt-in
              signed by server key (keytool+zipalign+apksigner in image, v3 verifies, no .idsig left);
              Check now in browser: auto-refresh → "no releases yet, only workflow builds" / "latest is v1.5.1";
              no page overflow desk/phone (long branch pill fixed)
              signing badges (signed / debug / signed by this server) + debug advice with sibling Stage;
              Settings → General: time zone + 12-hour (default) / 24-hour clock
              Builds: signing badge per test build (same key as releases / different key / debug / unsigned),
              Check signing on real heartrate commit: -apk = same key as releases, -debug = debug; nothing kept
              real artifact staged with build notes (run title/trigger/branch/link + full commit message)
              t330-adb fixed: key tmpfs uid 10001 (was 1000: no key, crashed on first connect); adbkey present,
              app → adb-server resolves, `adb devices` OK. Same flaw in the v3.2.0 zip320 smoke stack (disclosed)
  test artifact: ⬜ rebuild from the exact PR head commit before merge
🔒 SECURITY   ✅ 0 open — 0 Critical, 0 High
              new dep cryptography==50.0.1 (current, PyCA); pip-audit --strict clean (whole tree)
              bandit: only the 6 known B608 in db.py (unchanged fns) — withdrawn as before;
              ✅ fixed: 💡 my IN(...) f-string query in seal migration → per-key parameterized query
              ✅ fixed: 📝 identity lookup every poll doubled anonymous rate-limit use → only on new release/unpinned
              token: TOKEN_RE + checked with GitHub before save, never echoed/logged/in URL, sent only to api.github.com;
              artifact downloads: token dropped at storage redirect, redirect host allow-list (+blob.core.windows.net);
              fork-PR artifacts excluded (head_repository_id == pinned id); SECRET_KEY change fails closed
              ✅ fixed: 📝 Medium (pre-existing) — httpx INFO logged full download URLs incl. signed storage
                 tokens (release assets + artifacts) → httpx logger at WARNING; 0 sig= in logs after (verified)
              signing: password only via env (never argv), keystore 600 + dir 700, never overwritten, only truly
                 unsigned APKs (no v1 files, no signing block) are signed; broken signatures still refused
              Standards: at-rest ✅ now encrypted (TOTP, notify URLs, GitHub token, signing-key password)
📄 DOCS       ✅ CHANGELOG 3.3.0 (+ Settings split, token template/expiry/access); README Settings + GitHub token sections,
              Sources (review, artifacts), Source verification section, threat model row,
              Data at rest (encrypted + SECRET_KEY change), GitHub access, Configuration; .env.example; HANDOFF
              pre-existing inaccuracies fixed: ETag 304s only free when authenticated; Apprise guide said "unencrypted"
              ALL 16 screenshots retaken from invented data, GitHub mocked (scripts/screenshots/run.sh): no real
              repo/account/token/device in any; new repo-review.png + builds.png; README refs all resolve, none unused
📦 RELEASE    ⏳ commit approved + made on feat/repo-legitimacy (user: "commit here"); not pushed, no PR yet
              notes = CHANGELOG 3.3.0; next: push, PR, PR-head test images, then SHIP
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
