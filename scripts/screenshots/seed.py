"""Fills a fresh screenshot database with invented data (see mock_github.py
for the matching GitHub side). Run inside the app container:
    docker exec -i <app> python - base < seed.py
    docker exec -i <app> python - mfa  < seed.py   # two-factor on, for the security shots
Nothing here is real: repos, phones, serials, IPs and tokens are made up."""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")
os.chdir("/app")
import db  # noqa: E402
import mfa  # noqa: E402
import staging  # noqa: E402

NOW = datetime.now(timezone.utc)


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def sql(query: str, *args):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(query, args)
        conn.commit()
    finally:
        conn.close()


def stage(repo_id, tag, filename, package, version, code, abis, days, released=None, **kw):
    folder = staging.repo_dir(repo_id) if repo_id else os.path.join(staging.STAGING_ROOT, "uploads")
    os.makedirs(folder, exist_ok=True)
    sha = f"{abs(hash((tag, filename))):064x}"[-64:]
    path = os.path.join(folder, f"{sha}.apk")
    open(path, "wb").write(b"mock")
    apk_id = db.insert_staged_apk(repo_id=repo_id, tag=tag, filename=filename, sha256=sha, package_name=package,
                                  signer_sha256="5e" * 32, path=path, version_code=code, version_name=version,
                                  abis=abis, released_at=released, **kw)
    sql("UPDATE staged_apks SET downloaded_at = ? WHERE id = ?", ago(days), apk_id)
    return apk_id


def base():
    db.init_db()
    weather = db.create_repo("example", "weather-app", "*.apk", github_id=101, owner_id=11, owner_type="User")
    notes = db.create_repo("acme-labs", "field-notes", "*.apk", github_id=202, owner_id=22, owner_type="Organization")
    timer = db.create_repo("example", "pocket-timer", "*.apk", github_id=303, owner_id=11, owner_type="User")
    db.update_repo_check(weather, last_tag="v2.4.0", expected_package="com.example.weather", signer_sha256="5e" * 32)
    db.update_repo_check(notes, last_tag="v1.9.2", expected_package="com.acmelabs.fieldnotes", signer_sha256="7a" * 32)
    db.update_repo_check(timer)
    db.set_rejected_tag(notes, "v2.0.0", "Pin mismatch in v2.0.0: signed by a different certificate. Not staged.",
                        "com.acmelabs.fieldnotes", "9c" * 32, True)

    notes_24 = "## Radar layers\n- Rain, snow and lightning layers on the radar map\n- Faster start-up on older phones"
    w24a = stage(weather, "v2.4.0", "weather-app-arm64-v8a.apk", "com.example.weather", "2.4.0", 240, "arm64-v8a", 3,
                 ago(3), release_notes=notes_24)
    stage(weather, "v2.4.0", "weather-app-armeabi-v7a.apk", "com.example.weather", "2.4.0", 240, "armeabi-v7a", 3,
          ago(3), release_notes=notes_24)
    w231 = stage(weather, "v2.3.1", "weather-app.apk", "com.example.weather", "2.3.1", 231, "", 21, ago(21),
                 release_notes="Fixes the widget going blank after a reboot.")
    fn = stage(notes, "v1.9.2", "field-notes.apk", "com.acmelabs.fieldnotes", "1.9.2", 192, "", 9, ago(9),
               release_notes="- Sync retries on flaky connections\n- Photo attachments keep their location")

    art_notes = ("Build (run #118, push)\nfix/radar-cache @ c7d21f0 · https://github.com/example/weather-app/actions/runs/7001"
                 "\n\nCommit c7d21f0:\nfix(radar): keep tiles cached across rotation\n\nThe radar layer refetched every tile on rotate.")
    stage(None, "weather-app-release fix/radar-cache@c7d21f0", "weather-app-release.apk", "com.example.weather", "2.4.1-rc",
          241, "arm64-v8a armeabi-v7a", 0.2, source="upload", release_notes=art_notes,
          artifact={"repo": "example/weather-app", "run_id": 7001, "branch": "fix/radar-cache", "sha": "c7d21f09ab",
                    "subject": "fix(radar): keep tiles cached across rotation", "repo_id": weather,
                    "siblings": json.dumps([{"id": 52, "name": "weather-app-debug"}])})
    timer_debug = stage(None, "pocket-timer-debug feature/lap-sounds@3f9c2e1", "pocket-timer-debug.apk", "com.example.pockettimer",
          "0.8.0-debug", 80, "", 0.5, source="upload", is_debug=True,
          release_notes="CI (run #42, push)\nfeature/lap-sounds @ 3f9c2e1\n\nCommit 3f9c2e1:\nfeat: lap sounds with a volume slider",
          artifact={"repo": "example/pocket-timer", "run_id": 7102, "branch": "feature/lap-sounds", "sha": "3f9c2e1d88",
                    "subject": "feat: lap sounds with a volume slider", "repo_id": timer,
                    "siblings": json.dumps([{"id": 61, "name": "pocket-timer-release"}])})
    stage(None, "kiosk build", "kiosk-unsigned.apk", "com.example.kiosk", "3.0.0", 300, "", 2, source="upload",
          server_signed=True)

    for serial, addr, nick, model, abis, seen in (
        ("R5CX12AB34F", "192.168.1.41:37215", None, "Google Pixel 8", "arm64-v8a armeabi-v7a", 0.01),
        ("3B281FDJG000QK", "192.168.1.52:41093", "Kitchen tablet", "Samsung Galaxy Tab S9", "arm64-v8a", 0.3),
    ):
        db.upsert_paired_device(serial, addr)
        db.set_device_trusted(serial, True)
        if nick:
            db.set_device_nickname(serial, nick)
        db.set_device_model(serial, model)
        db.set_device_abis(serial, abis.split())
        sql("UPDATE devices SET last_seen_at = ?, paired_at = ? WHERE serial = ?", ago(seen), ago(60), serial)
    db.upsert_device_package("R5CX12AB34F", "com.example.weather", True, 231, "2.3.1")
    db.upsert_device_package("R5CX12AB34F", "com.acmelabs.fieldnotes", True, 192, "1.9.2")
    db.upsert_device_package("3B281FDJG000QK", "com.example.weather", True, 240, "2.4.0")
    db.upsert_device_package("3B281FDJG000QK", "com.acmelabs.fieldnotes", False)
    db.upsert_device_package("3B281FDJG000QK", "com.example.pockettimer", True, 80, "0.8.0-debug")
    db.upsert_device_package("R5CX12AB34F", "com.example.kiosk", True, 290, "2.9.0")  # sideloaded: not from here
    # Where each install came from: a release, a test build, and one pushed before tracking began.
    for serial, apk, days, extra in (("R5CX12AB34F", w231, 20, {}), ("3B281FDJG000QK", w24a, 2.9, {}),
                                     ("3B281FDJG000QK", timer_debug, 0.4, {}),
                                     ("R5CX12AB34F", fn, 8.5, {"update_time": None, "backfilled": True})):
        apk_row = db.get_staged_apk(apk)
        stamp = (NOW - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        origin = db.origin_of_apk(apk_row) | {"at": ago(days), "update_time": stamp} | extra
        sql("UPDATE device_packages SET origin = ?, update_time = ? WHERE device_serial = ? AND package_name = ?",
            json.dumps(origin), stamp, serial, apk_row["package_name"])
    db.set_follow("3B281FDJG000QK", weather, True)

    ok_log = "Performing Streamed Install\nSuccess"
    fail_log = ("Performing Streamed Install\nadb: failed to install weather-app.apk: Failure "
                "[INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package com.example.weather signatures do not match "
                "newer version; ignoring!]")
    for serial, apk, status, log, days in (("3B281FDJG000QK", w24a, "success", ok_log, 2.9),
                                           ("R5CX12AB34F", fn, "success", ok_log, 8.5),
                                           ("R5CX12AB34F", w231, "failed", fail_log, 1.2),
                                           ("R5CX12AB34F", w231, "success", ok_log, 20),
                                           ("3B281FDJG000QK", timer_debug, "success", ok_log, 0.4)):
        iid = db.insert_install(serial, apk, "running")
        db.finish_install(iid, status, log)
        sql("UPDATE installs SET started_at = ?, finished_at = ? WHERE id = ?", ago(days), ago(days - 0.001), iid)

    for artifact_id, kind, signer in ((51, "signed", "5e" * 32), (52, "debug", "d0" * 32), (53, "debug", "d0" * 32),
                                      (61, "signed", "3a" * 32), (62, "debug", "d0" * 32)):
        db.record_artifact_signing(artifact_id, weather if artifact_id < 60 else timer, kind, signer)
    db.add_notify_target("ntfy://ntfy.sh/example-topic", "phone")
    db.add_notify_target("gotifys://gotify.example.home/AbCdEf123456", None)
    db.set_secret("github_token", "github_pat_" + "EXAMPLE0" * 8)
    db.set_meta("github_token_expires", (NOW + timedelta(days=83)).isoformat())
    db.set_meta("timezone", "America/New_York")

    for action, detail, client, days in (
        ("login", "password + trusted browser", "192.168.1.20", 0.02),
        ("upload", "kiosk-unsigned.apk com.example.kiosk sha256=4c1e0b9a7f21 debug=False server_signed=True", "192.168.1.20", 2),
        ("push", "example/weather-app v2.4.0 (weather-app-arm64-v8a.apk) → Kitchen tablet", "192.168.1.20", 2.9),
        ("repo_add", "example/pocket-timer id=303 owner=User glob=*.apk", "192.168.1.20", 12),
        ("auto_update_on", "example/weather-app → Kitchen tablet", "192.168.1.20", 14),
        ("device_trust", "3B281FDJG000QK (Kitchen tablet)", "192.168.1.20", 30),
        ("login_failed", "wrong password", "192.168.1.77", 31),
        ("github_token_set", "login=example-user expires=in 83 days", "192.168.1.20", 7),
    ):
        db.insert_audit(action, detail, client)
        sql("UPDATE audit_log SET at = ? WHERE id = (SELECT MAX(id) FROM audit_log)", ago(days))


def two_factor():
    secret = "JBSWY3DPEHPK3PXP"  # the RFC test secret: this is a demo
    db.set_secret("mfa_secret", secret)
    codes = mfa.new_recovery_codes()
    db.add_trusted_browser("0" * 64, "Firefox on Linux", "192.168.1.20", (NOW + timedelta(days=24)).isoformat())
    db.add_trusted_browser("1" * 64, "Safari on iPhone", "192.168.1.33", (NOW + timedelta(days=9)).isoformat())
    for _ in range(2):  # leave 8 of 10 recovery codes
        db.use_recovery_code(mfa._hash(mfa._normalize_recovery(codes.pop())))


{"base": base, "mfa": two_factor}[sys.argv[1] if len(sys.argv) > 1 else "base"]()
print("seeded", sys.argv[1:] or ["base"])
