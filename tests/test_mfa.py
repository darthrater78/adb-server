import time

import pytest

import auth
import db
import mfa
import mfa_admin

PASSWORD = {"username": "operator", "password": "correct-horse-battery"}


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    monkeypatch.setattr(auth, "_failed_attempts", {})


def code(offset: int = 0) -> str:
    """The authenticator code `offset` steps from now (the window is ±1)."""
    return mfa._code_at(db.get_secret("mfa_secret"), int(time.time() // mfa.STEP_SECONDS) + offset)


@pytest.fixture
def enabled():
    """MFA on, as if set up just now (the current step already used)."""
    mfa.pending_secret(create=True)
    codes = mfa.enable(mfa._code_at(db.get_secret("mfa_pending_secret"), int(time.time() // mfa.STEP_SECONDS)))
    assert codes and mfa.enabled()
    return codes


def login(client, **extra):
    return client.post("/login", data={**PASSWORD, **extra}, follow_redirects=False)


# ---- TOTP ----

def test_rfc6238_vector():
    # RFC 6238 appendix B: SHA-1 key "12345678901234567890", T = 59s -> 94287082 (8 digits).
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert mfa._code_at(secret, 59 // 30) == "287082"


def test_window_and_garbage():
    secret = mfa.new_secret()
    now = 1_000_000_000
    for offset in (-1, 0, 1):
        assert mfa.matching_step(secret, mfa._code_at(secret, now // 30 + offset), now) is not None
    assert mfa.matching_step(secret, mfa._code_at(secret, now // 30 + 3), now) is None
    for junk in ("", "12345", "abcdef", "1234567"):
        assert mfa.matching_step(secret, junk, now) is None
    assert mfa.matching_step(secret, " ".join(mfa._code_at(secret, now // 30)), now) is not None  # spaces ok


def test_provisioning_uri():
    uri = mfa.provisioning_uri("ABCD", "operator")
    assert uri.startswith("otpauth://totp/ADB%20Server%3Aoperator?secret=ABCD&issuer=ADB%20Server")


# ---- setup ----

def test_setup_flow(authed):
    page = authed.get("/settings/mfa/setup")
    assert page.status_code == 200 and "<svg" in page.text and page.headers["cache-control"] == "no-store"
    secret = db.get_secret("mfa_pending_secret")
    assert mfa.format_secret(secret) in page.text

    wrong = authed.post("/settings/mfa/enable", data={"csrf_token": "test-csrf-token", "code": "000000"},
                        follow_redirects=False)
    assert "error=" in wrong.headers["location"] and not mfa.enabled()

    epoch = db.get_session_epoch()
    r = authed.post("/settings/mfa/enable", data={
        "csrf_token": "test-csrf-token", "code": mfa._code_at(secret, int(time.time() // 30)),
    }, follow_redirects=False)
    assert r.status_code == 200 and mfa.enabled() and db.get_meta("mfa_pending_secret") is None
    assert r.text.count("<li>") >= mfa.RECOVERY_CODE_COUNT and db.recovery_codes_left() == 10
    assert db.get_session_epoch() == epoch + 1  # other sessions signed out
    assert auth.COOKIE_NAME in r.cookies  # ...but not this one
    assert authed.get("/settings", follow_redirects=False).status_code == 200


def test_setup_needs_login(client):
    assert client.get("/settings/mfa/setup", follow_redirects=False).headers["location"] == "/login"


# ---- sign-in ----

def test_password_alone_is_not_enough(client, enabled):
    r = login(client)
    assert r.headers["location"] == "/login/mfa"
    assert auth.COOKIE_NAME not in r.cookies
    assert client.get("/status", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/login/mfa").status_code == 200


def test_code_signs_in(client, enabled):
    login(client)
    r = client.post("/login/mfa", data={"code": code(1)}, follow_redirects=False)
    assert r.headers["location"] == "/status" and auth.COOKIE_NAME in r.cookies
    assert auth.MFA_TRUST_COOKIE not in r.cookies
    assert client.get("/status").status_code == 200


def test_code_is_single_use(client, enabled):
    login(client)
    client.post("/login/mfa", data={"code": code(1)})
    client.cookies.clear()
    login(client)
    assert client.post("/login/mfa", data={"code": code(1)}).status_code == 401


def test_second_step_needs_the_password_first(client, enabled):
    r = client.post("/login/mfa", data={"code": code(1)}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert client.get("/login/mfa", follow_redirects=False).headers["location"] == "/login"


def test_trusted_browser_skips_the_code_until_revoked(authed, enabled):
    from fastapi.testclient import TestClient

    import main
    client = TestClient(main.app)  # a second browser; `authed` stays signed in
    login(client)
    r = client.post("/login/mfa", data={"code": code(1), "trust": "1"}, follow_redirects=False)
    assert auth.MFA_TRUST_COOKIE in r.cookies
    (browser,) = db.list_trusted_browsers()
    client.cookies.delete(auth.COOKIE_NAME)
    assert login(client).headers["location"] == "/status"  # no code asked

    authed.post("/settings/mfa/trusted/revoke", data={"csrf_token": "test-csrf-token", "browser": browser["token_hash"]})
    client.cookies.delete(auth.COOKIE_NAME)
    assert login(client).headers["location"] == "/login/mfa"


def test_forged_trust_cookie_is_ignored(client, enabled):
    client.cookies.set(auth.MFA_TRUST_COOKIE, "not-a-signed-token")
    assert login(client).headers["location"] == "/login/mfa"


def test_recovery_code_works_once(client, enabled):
    login(client)
    r = client.post("/login/mfa", data={"code": enabled[0].upper()}, follow_redirects=False)
    assert r.headers["location"].startswith("/status?ok=") and db.recovery_codes_left() == 9
    client.cookies.clear()
    login(client)
    assert client.post("/login/mfa", data={"code": enabled[0]}).status_code == 401


def test_lockout_and_unlock(client, enabled):
    login(client)
    for _ in range(mfa.MAX_FAILURES - 1):
        assert client.post("/login/mfa", data={"code": "000000"}).status_code == 401
    r = client.post("/login/mfa", data={"code": "000000"})
    assert r.status_code == 429 and "mfa_admin.py unlock" in r.text
    auth._failed_attempts.clear()  # a different IP: the lock is global, not per IP
    assert client.post("/login/mfa", data={"code": code(1)}).status_code == 429
    assert mfa_admin.main(["mfa_admin.py", "unlock"]) == 0
    r = client.post("/login/mfa", data={"code": code(1)}, follow_redirects=False)
    assert r.headers["location"] == "/status"


# ---- settings ----

def test_disable_needs_a_current_code(authed, enabled):
    r = authed.post("/settings/mfa/disable", data={"csrf_token": "test-csrf-token", "code": "000000"},
                    follow_redirects=False)
    assert "error=" in r.headers["location"] and mfa.enabled()
    authed.post("/settings/mfa/disable", data={"csrf_token": "test-csrf-token", "code": code(1)})
    assert not mfa.enabled() and db.recovery_codes_left() == 0


def test_new_recovery_codes_replace_the_old(authed, enabled):
    r = authed.post("/settings/mfa/recovery-codes", data={"csrf_token": "test-csrf-token", "code": code(1)})
    assert r.status_code == 200 and enabled[0] not in r.text
    assert mfa.check(enabled[0]) is None  # old code gone


def test_settings_actions_need_csrf(authed, enabled):
    r = authed.post("/settings/mfa/disable", data={"csrf_token": "wrong", "code": code(1)}, follow_redirects=False)
    assert r.status_code == 403 and mfa.enabled()


def test_revoke_rejects_bad_ids(authed, enabled):
    r = authed.post("/settings/mfa/trusted/revoke", data={"csrf_token": "test-csrf-token", "browser": "x' OR 1=1"},
                    follow_redirects=False)
    assert r.status_code == 400


def test_admin_reset_turns_it_off_and_signs_everyone_out(client, enabled):
    mfa.trust_browser("Firefox on Linux", None)
    epoch = db.get_session_epoch()
    assert mfa_admin.main(["mfa_admin.py", "reset"]) == 0
    assert not mfa.enabled() and db.list_trusted_browsers() == [] and db.get_session_epoch() == epoch + 1
    assert login(client).headers["location"] == "/status"


def test_admin_usage():
    assert mfa_admin.main(["mfa_admin.py"]) == 2


# ---- concurrency ----

def _in_parallel(fn, n: int = 16) -> list:
    import threading

    barrier, results = threading.Barrier(n), []

    def run():
        barrier.wait()
        results.append(fn())
    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_a_code_spent_in_parallel_works_once(enabled):
    fresh = code(1)
    assert _in_parallel(lambda: mfa.check(fresh)).count("totp") == 1


def test_parallel_guesses_stay_within_the_budget(enabled, monkeypatch):
    checked = []
    real = mfa.matching_step
    monkeypatch.setattr(mfa, "matching_step", lambda *a: checked.append(1) or real(*a))
    assert _in_parallel(lambda: mfa.check("000000"), 24) == [None] * 24
    assert len(checked) <= mfa.MAX_FAILURES
    assert mfa.locked_for() > 0


def test_attempts_past_the_budget_are_not_checked(enabled):
    db.set_meta("mfa_failures", str(mfa.MAX_FAILURES))
    assert mfa.check(code(1)) is None  # even a right code, once the budget is spent


def test_advance_meta_only_moves_forward():
    assert db.advance_meta("k", 5)
    assert not db.advance_meta("k", 5) and not db.advance_meta("k", 4)
    assert db.advance_meta("k", 6) and db.get_meta("k") == "6"
