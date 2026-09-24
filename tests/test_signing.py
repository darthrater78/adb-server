"""Unsigned APKs: refused unless the source opted in, then signed with this
server's own key."""
import io
import os
import subprocess
import zipfile

import pytest

import apk_verify
import db
import main
import signing
from conftest import CSRF


def _apk(extra: dict[str, bytes] | None = None, signing_block: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("AndroidManifest.xml", date_time=(2020, 1, 1, 0, 0, 0)), b"m")
        for name, data in (extra or {}).items():
            z.writestr(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), data)
    data = buf.getvalue()
    if signing_block:
        # Put a fake APK Signing Block right before the central directory.
        end = data.rfind(b"PK\x05\x06")
        cd = int.from_bytes(data[end + 16:end + 20], "little")
        block = b"\0" * 24 + b"APK Sig Block 42"
        data = bytearray(data[:cd] + block + data[cd:])
        new_end = data.rfind(b"PK\x05\x06")
        data[new_end + 16:new_end + 20] = (cd + len(block)).to_bytes(4, "little")
        data = bytes(data)
    return data


@pytest.mark.parametrize("extra,block,unsigned", [
    (None, False, True),
    ({"META-INF/CERT.RSA": b"x", "META-INF/CERT.SF": b"x"}, False, False),  # v1 signed
    ({"META-INF/MANIFEST.MF": b"x"}, False, True),  # a manifest alone is not a signature
    (None, True, False),  # v2+ signing block
])
def test_unsigned_means_no_signature_of_any_kind(tmp_path, extra, block, unsigned):
    path = tmp_path / "a.apk"
    path.write_bytes(_apk(extra, block))
    assert apk_verify.is_unsigned(str(path)) is unsigned


# ---- the signing module ----

@pytest.fixture
def tools(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, capture_output, text, errors, env, timeout, check):
        calls.append((cmd, env.get(signing._PASS_ENV)))
        if cmd[0] == "keytool" and "-genkeypair" in cmd:
            open(cmd[cmd.index("-keystore") + 1], "wb").write(b"p12")
        if cmd[0] == "zipalign":
            open(cmd[-1], "wb").write(open(cmd[-2], "rb").read())
        if cmd[0] == "apksigner":
            open(cmd[cmd.index("--out") + 1], "wb").write(b"signed:" + open(cmd[-1], "rb").read())
        if cmd[0] == "keytool" and "-list" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "Certificate fingerprint (SHA-256): AB:CD:EF\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(signing.subprocess, "run", fake_run)
    return calls


def test_first_signing_creates_the_key_and_never_puts_the_password_on_a_command_line(tools, tmp_path):
    apk = tmp_path / "a.apk"
    apk.write_bytes(b"apk")
    signing.sign_in_place(str(apk), signing.UPLOADS)
    password = db.get_secret("signing_key_password:uploads")
    assert password and apk.read_bytes() == b"signed:apk"
    assert [c[0][0] for c in tools] == ["keytool", "zipalign", "apksigner"]
    for cmd, env_pass in tools:
        assert password not in " ".join(cmd) and env_pass == password
    assert os.stat(signing.keystore_path(signing.UPLOADS)).st_mode & 0o777 == 0o600
    assert sorted(os.listdir(tmp_path)) == ["a.apk", "app.db", "signing"] or "a.apk.signed" not in os.listdir(tmp_path)


def test_the_key_is_made_once(tools, tmp_path):
    for n in range(2):
        apk = tmp_path / f"{n}.apk"
        apk.write_bytes(b"apk")
        signing.sign_in_place(str(apk), signing.UPLOADS)
    assert sum(1 for cmd, _ in tools if "-genkeypair" in cmd) == 1


def test_a_keystore_without_its_password_is_never_overwritten(tools, tmp_path):
    path = signing.keystore_path(signing.UPLOADS)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(b"old key")
    with pytest.raises(signing.SigningError, match="password is missing"):
        signing.sign_in_place(str(tmp_path / "a.apk"), signing.UPLOADS)
    assert open(path, "rb").read() == b"old key"


def test_fingerprint(tools, tmp_path):
    assert signing.key_fingerprint(signing.UPLOADS) is None
    apk = tmp_path / "a.apk"
    apk.write_bytes(b"apk")
    signing.sign_in_place(str(apk), signing.UPLOADS)
    assert signing.key_fingerprint(signing.UPLOADS) == "abcdef"


def test_each_source_gets_its_own_key(tools, tmp_path):
    """A shared key would let one opted-in source ship a build under another
    source's package name and replace that app on the phone."""
    for n, source in enumerate((signing.repo_source(11), signing.repo_source(22), signing.UPLOADS)):
        apk = tmp_path / f"{n}.apk"
        apk.write_bytes(b"apk")
        signing.sign_in_place(str(apk), source)
    made = [cmd[cmd.index("-keystore") + 1] for cmd, _ in tools if "-genkeypair" in cmd]
    assert sorted(os.path.basename(m) for m in made) == ["github-11.p12", "github-22.p12", "uploads.p12"]
    passwords = {db.get_secret(f"signing_key_password:{s}") for s in ("github-11", "github-22", "uploads")}
    assert len(passwords) == 3 and None not in passwords
    stores = {cmd[cmd.index("--ks") + 1] for cmd, _ in tools if cmd[0] == "apksigner"}
    assert len(stores) == 3
    assert signing.sources() == ["uploads", "github-11", "github-22"]


@pytest.mark.parametrize("source", ["", "server-key", "github-", "github-1/../x", "../uploads", "github-1a"])
def test_a_source_that_isnt_one_is_refused(tools, tmp_path, source):
    with pytest.raises(signing.SigningError):
        signing.sign_in_place(str(tmp_path / "a.apk"), source)
    assert tools == []


# ---- uploads ----

@pytest.fixture
def verify(monkeypatch):
    monkeypatch.setattr(apk_verify, "verify_signature", lambda path: apk_verify.SignerInfo("5" * 64, False))
    monkeypatch.setattr(apk_verify, "get_package_info",
                        lambda path: apk_verify.PackageInfo("com.example.app", 1, "1.0", ()))
    signed = []

    def sign(path, source):
        signed.append((path, source))
        with open(path, "ab") as f:
            f.write(b"signed")
    monkeypatch.setattr(signing, "sign_in_place", sign)
    return signed


def _upload(authed, content, **extra):
    return authed.post("/staged/upload", data={"csrf_token": CSRF, "label": "", **extra},
                       files={"apk": ("app-release-unsigned.apk", content, "application/vnd.android.package-archive")},
                       follow_redirects=False)


def test_an_unsigned_upload_is_refused_with_the_reason(authed, verify):
    r = _upload(authed, _apk())
    assert "unsigned" in r.headers["location"] and "can%27t%20install" in r.headers["location"]
    assert db.list_staged_apks() == [] and verify == []


def test_an_unsigned_upload_is_signed_when_opted_in(authed, verify):
    r = _upload(authed, _apk(), sign_unsigned="yes")
    assert "signed%20with%20this%20server" in r.headers["location"]
    [apk] = db.list_staged_apks()
    assert apk["server_signed"] == 1 and [s for _, s in verify] == [signing.UPLOADS]
    with open(apk["path"], "rb") as f:
        import hashlib
        assert apk["sha256"] == hashlib.sha256(f.read()).hexdigest()  # the signed file's hash
    assert any("server_signed=True" in a["detail"] for a in db.list_audit())
    assert "signed by this server" in authed.get("/install").text


def test_a_signed_upload_is_never_re_signed(authed, verify):
    _upload(authed, _apk({"META-INF/CERT.RSA": b"x", "META-INF/CERT.SF": b"x"}), sign_unsigned="yes")
    [apk] = db.list_staged_apks()
    assert apk["server_signed"] == 0 and verify == []


# ---- per-repo opt-in ----

def test_the_repo_opt_in_needs_the_explanation_acknowledged(authed):
    rid = db.create_repo("o", "r", "*.apk", github_id=1, owner_id=2, owner_type="User")
    r = authed.post(f"/repos/{rid}/sign-unsigned", data={"csrf_token": CSRF, "on": "1"}, follow_redirects=False)
    assert "understand" in r.headers["location"] and db.get_repo(rid)["sign_unsigned"] == 0
    authed.post(f"/repos/{rid}/sign-unsigned", data={"csrf_token": CSRF, "on": "1", "understood": "yes"})
    assert db.get_repo(rid)["sign_unsigned"] == 1
    assert any(a["action"] == "sign_unsigned_on" for a in db.list_audit())
    authed.post(f"/repos/{rid}/sign-unsigned", data={"csrf_token": CSRF, "on": "0"})
    assert db.get_repo(rid)["sign_unsigned"] == 0


def test_explanation_is_shown_where_the_opt_in_is_offered(authed):
    assert "can&#39;t install an unsigned APK" in authed.get("/sources").text


def test_signing_leaves_no_side_files(tools, tmp_path):
    apk = tmp_path / "a.apk"
    apk.write_bytes(b"apk")
    signing.sign_in_place(str(apk), signing.UPLOADS)
    [sign_cmd] = [c for c, _ in tools if c[0] == "apksigner"]
    assert sign_cmd[sign_cmd.index("--v4-signing-enabled") + 1] == "false"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("a.apk")) == ["a.apk"]


def test_settings_lists_each_sources_key_by_name(authed, tools, tmp_path):
    db.create_repo("o", "r", "*.apk", github_id=11, owner_id=2, owner_type="User")
    for n, source in enumerate((signing.repo_source(11), signing.repo_source(99), signing.UPLOADS)):
        apk = tmp_path / f"{n}.apk"
        apk.write_bytes(b"apk")
        signing.sign_in_place(str(apk), source)
    page = authed.get("/settings/security").text
    assert "Uploads" in page and "o/r" in page and "A removed repo (GitHub ID 99)" in page
    assert page.count("SHA-256 abcdef") == 3
