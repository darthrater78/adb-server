import re

import pytest

import db
from test_features import _stage


@pytest.fixture
def two_devices():
    for serial, name in (("AAA", "Pixel"), ("BBB", "Tablet")):
        db.upsert_paired_device(serial, f"192.168.1.{len(name)}:37000")
        db.set_device_trusted(serial, True)
        db.set_device_nickname(serial, name)
    db.upsert_paired_device("EVIL", "192.168.1.99:37000")  # paired, never trusted


def _targets(page: str) -> set[str]:
    return set(re.findall(r'name="device_serial" value="([^"]*)"', page))


def test_every_push_goes_to_the_newest_trusted_device_by_default(authed, two_devices):
    _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    page = authed.get("/apps").text
    assert _targets(page) == {"BBB"}  # paired last
    assert "Pushing to</span> <strong>Tablet</strong>" in page and "<select" not in page


def test_the_picker_switches_the_target_device(authed, two_devices):
    _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    page = authed.get("/apps?to=AAA").text
    assert _targets(page) == {"AAA"}
    assert 'href="/apps?to=BBB"' in page and 'href="/apps?to=AAA" aria-current="true"' in page


@pytest.mark.parametrize("to", ["EVIL", "nope", "", "<b>x</b>"])
def test_the_picker_only_targets_trusted_devices(authed, two_devices, to):
    _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    page = authed.get("/apps", params={"to": to}).text
    assert _targets(page) == {"BBB"} and "EVIL" not in page


def test_one_device_needs_no_menu(authed):
    db.upsert_paired_device("AAA", "192.168.1.5:37000")
    db.set_device_trusted("AAA", True)
    _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    page = authed.get("/apps").text
    assert "push-target-menu" not in page and _targets(page) == {"AAA"}


def test_a_card_shows_its_essentials_and_hides_the_rest(authed, two_devices):
    rid = db.create_repo("o", "r", "*.apk")
    apk_id = _stage(rid, "v2", "app.apk", version_code=2, version_name="2.0")
    page = authed.get("/apps").text
    card = page[page.index(f'id="repo-{rid}"'):]
    head, more = card.split('<details class="app-card-more">', 1)
    assert 'Push<span class="push-what"> 2.0</span></a>' in head and "Latest release" in head and "any CPU" in head
    assert f'id="apk-{apk_id}"' in more and "Delete file" in more


def _head(page, rid):
    """A card's row, without its confirm dialog (which names the target)."""
    head = page.split(f'id="repo-{rid}"', 1)[1].split("app-card-more", 1)[0]
    return head.split('class="modal"', 1)[0]


def test_a_card_says_which_devices_already_have_it(authed, two_devices):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk", version_code=2, version_name="2.0")
    db.upsert_device_package("BBB", "com.example", True, 2, "2.0")
    db.upsert_device_package("AAA", "com.example", True, 1, "1.0")  # older: not "on"
    db.upsert_device_package("EVIL", "com.example", True, 2, "2.0")  # untrusted: not shown
    def head(query=""):
        return _head(authed.get("/apps" + query).text, rid)
    assert "1.0, update available" in head("?to=AAA") and "· also on Tablet</span>" in head("?to=AAA")
    assert "also on Pixel" not in head("?to=AAA") and "EVIL" not in head("?to=AAA")
    # The target itself isn't listed again: its own line already says so.
    assert "2.0 ✓" in head() and "also on" not in head()


def test_no_trusted_device_means_no_picker_and_no_push(authed):
    _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    page = authed.get("/apps").text
    assert "push-target" not in page and _targets(page) == set()
    assert "Delete file" in page


def _upload(tag="dev build"):
    import os
    import staging
    path = os.path.join(staging.STAGING_ROOT, "uploads", f"{tag}.apk")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(b"PK")
    return db.insert_staged_apk(repo_id=None, tag=tag, filename=f"{tag}.apk", sha256="e" * 64,
                                package_name="com.example.dev", signer_sha256="d" * 64, path=path, source="upload")


def test_repos_then_test_builds_then_uploads(authed, two_devices):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    up = _upload()
    page = authed.get("/apps?to=AAA").text
    assert page.index(f'id="repo-{rid}"') < page.index('id="kind-upload"') < page.index(f'id="upload-{up}"')
    assert 'href="/apps?to=BBB"' in page  # the device menu


def test_one_kind_needs_no_filter(authed, two_devices):
    _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    assert "filter-pills" not in authed.get("/apps").text


@pytest.mark.parametrize("have,text,solid", [
    (None, "not installed", True), ((1, "1.0"), "1.0, update available", True), ((2, "2.0"), "2.0 ✓", False)])
def test_a_row_says_what_the_target_has(authed, two_devices, have, text, solid):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk", version_code=2, version_name="2.0")
    if have:
        db.upsert_device_package("BBB", "com.example", True, *have)
    head = _head(authed.get("/apps").text, rid)
    assert f"On Tablet:" in head and text in head
    # The push is the solid button only when it would change something.
    assert (f'href="#push-repo-{rid}" class="button-link push-link">Push<span class="push-what"> 2.0' in head) == solid
