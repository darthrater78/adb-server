"""The check that the adb-server container and the web app are one release."""
import socket
import subprocess
import threading

import pytest

import adb_client
import db
import notify
import versions

_real_client_protocol = versions.client_protocol  # cached; sides() swaps in a plain function


@pytest.fixture
def info_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(versions, "INFO_DIR", str(tmp_path / "adbinfo"))
    (tmp_path / "adbinfo").mkdir()
    return tmp_path / "adbinfo"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    _real_client_protocol.cache_clear()
    monkeypatch.setattr(versions, "_last", [])
    yield
    _real_client_protocol.cache_clear()


def _adb_server(monkeypatch, reply: bytes):
    """A one-shot fake adb server answering host:version with `reply`."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    got = []

    def serve():
        conn, _ = listener.accept()
        with conn:
            got.append(conn.recv(64))
            conn.sendall(reply)
        listener.close()
    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setattr(adb_client, "ADB_HOST", "127.0.0.1")
    monkeypatch.setattr(adb_client, "ADB_PORT", str(listener.getsockname()[1]))
    return got


def _client(monkeypatch, output: str):
    monkeypatch.setattr(versions.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, output, ""))


# ---- reading each side ----

def test_image_version_is_read_from_the_shared_directory(info_dir):
    (info_dir / "version").write_text("3.4.0\n")
    assert versions.adb_image_version() == "3.4.0"


@pytest.mark.parametrize("content", ["", "latest", "3.4", "../../etc", "3.4.0; rm -rf /", "9" * 70])
def test_anything_but_a_version_is_ignored(info_dir, content):
    (info_dir / "version").write_text(content)
    assert versions.adb_image_version() is None


def test_no_directory_means_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(versions, "INFO_DIR", str(tmp_path / "missing"))
    assert versions.adb_image_version() is None


def test_server_protocol_speaks_host_version(monkeypatch):
    got = _adb_server(monkeypatch, b"OKAY00040029")
    assert versions.server_protocol() == 41
    assert got == [b"000chost:version"]


@pytest.mark.parametrize("reply", [b"FAIL0004nope", b"OKAYzzzz", b"OKAY0000", b"OK"])
def test_a_strange_answer_is_unknown(monkeypatch, reply):
    _adb_server(monkeypatch, reply)
    assert versions.server_protocol() is None


def test_an_unreachable_server_is_unknown(monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens there now
    monkeypatch.setattr(adb_client, "ADB_HOST", "127.0.0.1")
    monkeypatch.setattr(adb_client, "ADB_PORT", str(port))
    assert versions.server_protocol() is None


def test_client_protocol_from_adb_version(monkeypatch):
    _client(monkeypatch, "Android Debug Bridge version 1.0.41\nVersion 37.0.1-14059154\n")
    assert versions.client_protocol() == 41


# ---- the verdict ----

@pytest.fixture
def sides(info_dir, monkeypatch):
    def set_(image=versions.APP_VERSION, server=41, client=41):
        if image is None:
            (info_dir / "version").unlink(missing_ok=True)
        else:
            (info_dir / "version").write_text(image)
        monkeypatch.setattr(versions, "server_protocol", lambda: server)
        monkeypatch.setattr(versions, "client_protocol", lambda: client)
    return set_


def test_same_release_is_no_problem(sides):
    sides()
    assert versions.check().problems == []


def test_an_older_adb_server_image_is_named(sides):
    sides(image="3.2.0")
    [problem] = versions.check().problems
    assert "runs 3.2.0" in problem and f"web app is {versions.APP_VERSION}" in problem
    assert versions.UPDATE_BOTH in problem


def test_a_mounted_directory_with_no_version_means_an_old_adb_server_image(sides):
    sides(image=None)
    [problem] = versions.check().problems
    assert "older than 3.4.0" in problem and versions.UPDATE_BOTH in problem


def test_no_shared_directory_says_to_mount_it(sides, tmp_path, monkeypatch):
    sides()
    monkeypatch.setattr(versions, "INFO_DIR", str(tmp_path / "not-mounted"))
    [problem] = versions.check().problems
    assert "Can't tell" in problem and "adbinfo" in problem


def test_a_protocol_mismatch_is_flagged_on_its_own(sides):
    sides(server=40, client=41)
    [problem] = versions.check().problems
    assert "protocol 40" in problem and "speaks 41" in problem


def test_an_unreachable_adb_server_is_not_a_version_problem(sides):
    sides(server=None)
    assert versions.check().problems == []


def test_alerts_once_per_problem_and_again_after_it_was_fixed(sides, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda event, title, body: sent.append(event))
    sides(image="3.2.0")
    versions.check_and_alert()
    versions.check_and_alert()
    assert sent == ["version_mismatch"]
    sides()
    versions.check_and_alert()
    assert db.get_meta("version_alerted") is None
    sides(image="3.2.0")
    versions.check_and_alert()
    assert sent == ["version_mismatch", "version_mismatch"]


# ---- where it shows ----

def test_every_page_warns_while_out_of_step(authed, sides):
    sides(image="3.2.0")
    versions.check_and_alert()
    assert "runs 3.2.0" in authed.get("/status").text
    assert "runs 3.2.0" in authed.get("/sources").text
    authed.cookies.clear()
    assert "runs 3.2.0" not in authed.get("/login").text


def test_no_warning_when_they_agree(authed, sides):
    sides()
    versions.check_and_alert()
    assert "out of step" not in authed.get("/status").text.lower()


def test_settings_general_shows_both_versions(authed, sides):
    sides(image="3.2.0", server=41, client=41)
    versions.check_and_alert()
    page = authed.get("/settings/general").text
    assert "adb-server container" in page and "3.2.0" in page and "server 41" in page


def test_the_event_can_be_chosen(authed):
    assert "version_mismatch" in notify.EVENTS
    assert "different releases" in authed.get("/settings/notifications").text
