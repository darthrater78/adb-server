import pytest

import adb_client
import apk_verify
import auth
import db
import github_client

A, B, PK = "a" * 64, "b" * 64, "c" * 64


def _signer_block(n, cn, cert, pubkey=PK):
    return (f"Signer #{n} certificate DN: CN={cn}\n"
            f"Signer #{n} certificate SHA-256 digest: {cert}\n"
            f"Signer #{n} public key SHA-256 digest: {pubkey}\n")


def test_parse_signers_single_signer_keeps_bare_fingerprint():
    info = apk_verify.parse_signers(_signer_block(1, "Release", A))
    assert info == apk_verify.SignerInfo(A, False)


def test_parse_signers_ignores_public_key_digest_regardless_of_order():
    out = f"Signer #1 public key SHA-256 digest: {PK}\nSigner #1 certificate SHA-256 digest: {A}\n"
    assert apk_verify.parse_signers(out).fingerprint == A


def test_parse_signers_covers_every_signer():
    info = apk_verify.parse_signers(_signer_block(1, "Release", B) + _signer_block(2, "Android Debug", A))
    assert info.fingerprint == f"{A},{B}"
    assert info.debug  # debug cert on the second signer is still seen


def test_parse_signers_without_certificate_digest_fails():
    with pytest.raises(apk_verify.ApkVerifyError):
        apk_verify.parse_signers(f"Signer #1 public key SHA-256 digest: {PK}\n")


def test_assert_apk_container(tmp_path):
    import zipfile
    good, bad, nomanifest = tmp_path / "g.apk", tmp_path / "b.apk", tmp_path / "n.apk"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("AndroidManifest.xml", b"x")
    with zipfile.ZipFile(nomanifest, "w") as z:
        z.writestr("other", b"x")
    bad.write_bytes(b"not a zip")
    apk_verify.assert_apk_container(str(good))
    for p in (bad, nomanifest):
        with pytest.raises(apk_verify.ApkVerifyError):
            apk_verify.assert_apk_container(str(p))


@pytest.mark.parametrize("url", [
    "https://objects.githubusercontent.com/x?sig=1",
    "https://release-assets.githubusercontent.com/x",
    "https://github.com/o/r/releases/download/v1/a.apk",
])
def test_redirect_allowed(url):
    assert github_client._validated_redirect(url) == url


@pytest.mark.parametrize("url", [
    "http://objects.githubusercontent.com/x",
    "https://evil.example/x",
    "https://githubusercontent.com.evil.example/x",
    "https://evilgithub.com/x",
])
def test_redirect_refused(url):
    with pytest.raises(github_client.GithubError):
        github_client._validated_redirect(url)


def test_rate_limiter_sweeps_expired_clients(monkeypatch):
    monkeypatch.setattr(auth, "_failed_attempts", {f"10.0.0.{i}": [0.0] for i in range(50)})
    auth._sweep(auth.WINDOW_SECONDS + 1)
    assert auth._failed_attempts == {}


def test_rate_limiter_enforces_ceiling(monkeypatch):
    monkeypatch.setattr(auth, "MAX_TRACKED_CLIENTS", 3)
    monkeypatch.setattr(auth, "_failed_attempts", {str(i): [float(i)] for i in range(5)})
    auth._sweep(5.0)
    assert set(auth._failed_attempts) == {"2", "3", "4"}  # least-recently-seen evicted


def test_login_refuses_foreign_origin(client):
    r = client.post("/login", data={"username": "operator", "password": "correct-horse-battery"},
                    headers={"Origin": "https://evil.example"}, follow_redirects=False)
    assert r.status_code == 403


def test_login_accepts_null_origin(client):
    r = client.post("/login", data={"username": "operator", "password": "correct-horse-battery"},
                    headers={"Origin": "null"}, follow_redirects=False)
    assert r.status_code == 303


@pytest.mark.parametrize("addr,expected", [
    ("192.168.1.5:5555", ("192.168.1.5", "5555")),
    ("[fd00::5]:37251", ("fd00::5", "37251")),
])
def test_split_host_port(addr, expected):
    assert adb_client.split_host_port(addr) == expected
    assert adb_client.join_host_port(*expected) == addr


def test_validate_addr_accepts_bracketed_private_ipv6():
    assert adb_client._validate_addr("[fd00::5]:5555") == "[fd00::5]:5555"


@pytest.mark.parametrize("addr", ["fd00::5:5555", "[fd00::5]5555", "[2001:4860::1]:5555"])
def test_validate_addr_rejects_bad_ipv6(addr):
    with pytest.raises(adb_client.AdbError):
        adb_client._validate_addr(addr)


def test_indexes_exist():
    with db.get_conn() as conn:
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_staged_apks_repo_id", "idx_installs_apk_id", "idx_installs_started_at"} <= names


# ---- request body guard (pre-auth disk fill) ----

def test_unauthenticated_upload_is_refused_before_the_body_is_read(client):
    r = client.post("/staged/upload", data={"csrf_token": "x"},
                    files={"apk": ("a.apk", b"PK" + b"\0" * 1000, "application/octet-stream")},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


@pytest.mark.parametrize("path", ["/login", "/repos", "/theme"])
def test_multipart_is_refused_outside_the_upload_route(client, path):
    r = client.post(path, data={"username": "a", "password": "b"},
                    files={"junk": ("x.bin", b"\0" * 1000, "application/octet-stream")})
    assert r.status_code == 415


def test_oversized_form_is_refused(client):
    r = client.post("/login", data={"username": "a", "password": "b" * (70 * 1024)})
    assert r.status_code == 413


def test_chunked_body_is_refused(client):
    r = client.post("/login", content=iter([b"username=a&password=b"]),
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 411


# ---- credentials and sessions ----

@pytest.mark.parametrize("username,password", [("é", "x"), ("operator", "pässwörd")])
def test_non_ascii_credentials_are_a_clean_401(client, username, password):
    r = client.post("/login", data={"username": username, "password": password})
    assert r.status_code == 401


def test_non_ascii_csrf_token_is_a_clean_403(authed):
    r = authed.post("/repos", data={"csrf_token": "tökén", "repo_url": "o/r"})
    assert r.status_code == 403


def test_logout_revokes_copies_of_the_session_cookie(authed):
    from conftest import CSRF as token
    stolen = dict(authed.cookies)
    authed.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    authed.cookies.clear()
    for k, v in stolen.items():
        authed.cookies.set(k, v)
    r = authed.get("/status", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_fresh_login_works_after_logout(client):
    auth.revoke_sessions()
    r = client.post("/login", data={"username": "operator", "password": "correct-horse-battery"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/status", follow_redirects=False).status_code == 200


# ---- robustness ----

def test_interrupted_installs_are_failed_at_startup(tmp_path):
    rid = db.create_repo("o", "r", "*.apk")
    apk_id = db.insert_staged_apk(rid, "v1", "a.apk", "0" * 64, "com.x", A, str(tmp_path / "a"))
    db.upsert_paired_device("SER", "192.168.1.50:5555")
    stuck = db.insert_install("SER", apk_id, "installing")
    done = db.insert_install("SER", apk_id, "pending")
    db.finish_install(done, "success", "ok")
    assert db.fail_interrupted_installs() == 1
    assert db.get_install(stuck)["status"] == "failed"
    assert "Interrupted" in db.get_install(stuck)["log"]
    assert db.get_install(done)["status"] == "success"


def test_asset_url_off_the_api_host_is_refused_without_a_request(tmp_path):
    import asyncio
    with pytest.raises(github_client.GithubError, match="api.github.com"):
        asyncio.run(github_client.download_asset(
            {"name": "a.apk", "url": "https://evil.example/a.apk"}, str(tmp_path / "a"), "secret-token"))


def test_network_errors_become_github_errors(monkeypatch):
    import asyncio
    import httpx

    class Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "AsyncClient", Boom)
    with pytest.raises(github_client.GithubError, match="Could not reach GitHub"):
        asyncio.run(github_client.get_latest_release("o", "r", None))


def test_httpx_request_urls_are_not_logged():
    # Download redirects carry signed URLs that work as read tokens.
    import logging

    import main  # noqa: F401 — importing it sets the level
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
