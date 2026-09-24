import pytest

import db
import web
from conftest import CSRF


def test_times_default_to_utc(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    web._display_zone.clear()
    assert "10:05 AM UTC" in web.when("2026-01-15T10:05:00+00:00")


def test_tz_from_the_environment_is_used(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    web._display_zone.clear()
    assert "11:05 AM CET" in web.when("2026-01-15T10:05:00+00:00")


def test_an_invalid_env_tz_falls_back_to_utc(monkeypatch):
    monkeypatch.setenv("TZ", "Mars/Olympus")
    web._display_zone.clear()
    assert "UTC" in web.when("2026-01-15T10:05:00+00:00")


def test_saving_a_zone_changes_every_time_shown(authed, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    r = authed.post("/settings/timezone", data={"csrf_token": CSRF, "tz": "America/New_York"}, follow_redirects=False)
    assert "ok=" in r.headers["location"] and db.get_meta("timezone") == "America/New_York"
    shown = str(web.when("2026-07-04T16:00:00+00:00"))
    assert "12:00 PM EDT" in shown and 'title="2026-07-04T16:00:00+00:00"' in shown  # exact UTC in the tooltip
    assert "America/New_York" in authed.get("/settings").text and "12-hour" in authed.get("/settings").text
    assert '<option value="America/New_York" selected>' in authed.get("/settings/general").text
    assert "Time zone" not in authed.get("/settings/appearance").text
    assert any(a["action"] == "timezone" for a in db.list_audit())


@pytest.mark.parametrize("bad", ["Mars/Olympus", "", "../../etc/passwd"])
def test_only_known_zones_can_be_saved(authed, bad):
    r = authed.post("/settings/timezone", data={"csrf_token": CSRF, "tz": bad}, follow_redirects=False)
    assert r.status_code in (303, 422) and db.get_meta("timezone") is None


def test_timezone_needs_csrf(authed):
    r = authed.post("/settings/timezone", data={"csrf_token": "no", "tz": "UTC"}, follow_redirects=False)
    assert r.status_code in (400, 403) and db.get_meta("timezone") is None


def test_github_style_z_timestamps_render(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    web._display_zone.clear()
    assert "UTC" in web.when("2026-09-24T13:04:00Z")


@pytest.mark.parametrize("utc,twelve,twentyfour", [
    ("2026-01-15T00:05:00+00:00", "12:05 AM", "00:05"),
    ("2026-01-15T12:30:00+00:00", "12:30 PM", "12:30"),
    ("2026-01-15T23:59:00+00:00", "11:59 PM", "23:59"),
    ("2026-01-15T09:00:00+00:00", "9:00 AM", "09:00"),
])
def test_twelve_hour_by_default_and_twenty_four_on_request(authed, monkeypatch, utc, twelve, twentyfour):
    monkeypatch.delenv("TZ", raising=False)
    assert f"{twelve} UTC" in str(web.when(utc))
    authed.post("/settings/timezone", data={"csrf_token": CSRF, "tz": "UTC", "clock": "24"})
    assert f"{twentyfour} UTC" in str(web.when(utc))
    assert 'value="24" checked' in authed.get("/settings/general").text


def test_an_unknown_clock_is_refused(authed):
    r = authed.post("/settings/timezone", data={"csrf_token": CSRF, "tz": "UTC", "clock": "36"}, follow_redirects=False)
    assert "error=" in r.headers["location"] and db.get_meta("clock") is None
