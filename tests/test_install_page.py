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
    page = authed.get("/install").text
    assert _targets(page) == {"BBB"}  # paired last
    assert "Pushing to <strong>Tablet</strong>" in page and "<select" not in page


def test_the_picker_switches_the_target_device(authed, two_devices):
    _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    page = authed.get("/install?to=AAA").text
    assert _targets(page) == {"AAA"}
    assert 'href="/install?to=BBB"' in page and 'href="/install?to=AAA" aria-current="true"' in page


@pytest.mark.parametrize("to", ["EVIL", "nope", "", "<b>x</b>"])
def test_the_picker_only_targets_trusted_devices(authed, two_devices, to):
    _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    page = authed.get("/install", params={"to": to}).text
    assert _targets(page) == {"BBB"} and "EVIL" not in page


def test_one_device_needs_no_menu(authed):
    db.upsert_paired_device("AAA", "192.168.1.5:37000")
    db.set_device_trusted("AAA", True)
    _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    page = authed.get("/install").text
    assert "push-target-menu" not in page and _targets(page) == {"AAA"}


def test_a_card_shows_its_essentials_and_hides_the_rest(authed, two_devices):
    rid = db.create_repo("o", "r", "*.apk")
    apk_id = _stage(rid, "v2", "app.apk", version_code=2, version_name="2.0")
    page = authed.get("/install").text
    card = page[page.index(f'id="app-{rid}"'):]
    head, more = card.split('<details class="app-card-more">', 1)
    assert "Push 2.0" in head and "Release" in head and "1 build" in head
    assert f'id="apk-{apk_id}"' in more and "Delete file" in more


def test_a_card_says_which_devices_already_have_it(authed, two_devices):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk", version_code=2, version_name="2.0")
    db.upsert_device_package("BBB", "com.example", True, 2, "2.0")
    db.upsert_device_package("AAA", "com.example", True, 1, "1.0")  # older: not "on"
    db.upsert_device_package("EVIL", "com.example", True, 2, "2.0")  # untrusted: not shown
    page = authed.get("/install").text
    head = page.split(f'id="app-{rid}"', 1)[1].split("app-card-more", 1)[0]
    assert "· on Tablet\n" in head and "Pixel" not in head and "EVIL" not in head


def test_no_trusted_device_means_no_picker_and_no_push(authed):
    _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    page = authed.get("/install").text
    assert "push-target" not in page and _targets(page) == set()
    assert "Delete file" in page
