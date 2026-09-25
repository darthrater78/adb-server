import pytest

import appearance
import db
import notify
from conftest import CSRF

TOKEN = "SuperSecretToken123456"
GOTIFY = f"gotifys://gotify.example.com/{TOKEN}"


# ---- appearance ----

@pytest.mark.parametrize("color", list(appearance.PRESETS.values()) + ["#ffff00", "#000000", "#ffffff", "#00ffcc"])
def test_accent_variants_are_readable_in_every_theme(color):
    v = appearance.variants(color)
    for bg in appearance.LIGHT_BGS:
        assert appearance.contrast(v["light"], bg) >= appearance.MIN_CONTRAST
    for bg in appearance.DARK_BGS:
        assert appearance.contrast(v["dark"], bg) >= appearance.MIN_CONTRAST
    assert appearance.contrast(v["on_light"], v["light"]) >= appearance.MIN_CONTRAST
    assert appearance.contrast(v["on_dark"], v["dark"]) >= appearance.MIN_CONTRAST


@pytest.mark.parametrize("name", list(appearance.PRESET_DARK))
def test_preset_dark_shades_are_readable(name):
    dark = appearance.PRESET_DARK[name]
    for bg in appearance.DARK_BGS:
        assert appearance.contrast(dark, bg) >= appearance.MIN_CONTRAST
    assert dark in appearance.stylesheet(appearance.PRESETS[name])


def test_every_preset_has_a_swatch_and_a_dark_shade():
    import os
    css = open(os.path.join(os.path.dirname(appearance.__file__), "static", "style.css")).read()
    assert set(appearance.PRESET_DARK) == set(appearance.PRESETS)
    for name, colour in appearance.PRESETS.items():
        assert f".swatch-{name} {{ background: {colour}; }}" in css


@pytest.mark.parametrize("bad", ["", "red", "#fff", "#12345g", "#1234567", "#123456;}body{x"])
def test_accent_rejects_anything_but_hex(bad):
    with pytest.raises(ValueError):
        appearance.normalize(bad)


def test_custom_accent_sets_accent_and_links(client, authed):
    assert "default accent" in client.get("/accent.css").text
    r = authed.post("/settings/appearance", data={"csrf_token": CSRF, "accent": "#7C3AED"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    assert db.get_meta("accent_color") == "#7c3aed"
    css = client.get("/accent.css")
    assert css.headers["content-type"].startswith("text/css")
    assert "--accent: #7c3aed" in css.text and "--link: #7c3aed" in css.text
    assert "--accent-2" not in css.text
    assert "/accent.css?v=7c3aed" in authed.get("/status").text


def test_a_second_colour_from_before_is_forgotten(authed):
    db.set_meta("accent_color", "#2563eb")
    db.set_meta("accent2_color", "#f59e0b")
    authed.post("/settings/appearance", data={"csrf_token": CSRF, "preset": "violet"})
    assert db.get_meta("accent_color") == appearance.PRESETS["violet"] and db.get_meta("accent2_color") is None


def test_preset(authed):
    authed.post("/settings/appearance", data={"csrf_token": CSRF, "preset": "graphite"})
    assert db.get_meta("accent_color") == appearance.PRESETS["graphite"]
    assert 'preset current' in authed.get("/settings/appearance").text


def test_default_preset_clears_the_setting(authed):
    db.set_meta("accent_color", "#2563eb")
    authed.post("/settings/appearance", data={"csrf_token": CSRF, "preset": appearance.DEFAULT_PRESET})
    assert db.get_meta("accent_color") is None


def test_unknown_preset_is_refused(authed):
    r = authed.post("/settings/appearance", data={"csrf_token": CSRF, "preset": "neon"}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_the_page_offers_one_colour(authed):
    page = authed.get("/settings/appearance").text
    assert 'name="accent"' in page and 'name="accent2"' not in page and "<strong>Secondary" not in page


def test_save_colour_stores_applies_and_lists_it(client, authed):
    r = authed.post("/settings/appearance/save", data={
        "csrf_token": CSRF, "name": "  Sunset  ", "accent": "#C2410C",
    }, follow_redirects=False)
    assert "ok=" in r.headers["location"] and r.headers["location"].startswith("/settings/appearance")
    (row,) = db.list_saved_colours()
    assert (row["name"], row["primary_color"]) == ("Sunset", "#c2410c")
    assert db.get_meta("accent_color") == "#c2410c"
    page = authed.get("/settings/appearance").text
    assert "Sunset" in page and f"swatch-saved-{row['id']}" in page
    assert f".swatch-saved-{row['id']} {{ background: #c2410c; }}" in client.get("/accent.css").text


def test_saving_an_existing_name_updates_it(authed):
    for accent in ("#111111", "#222222"):
        authed.post("/settings/appearance/save", data={"csrf_token": CSRF, "name": "Mine", "accent": accent})
    (row,) = db.list_saved_colours()
    assert row["primary_color"] == "#222222"


def test_a_pair_saved_before_applies_its_first_colour(authed):
    colour_id = db.save_colour("Old pair", "#111111", "#333333")
    authed.post("/settings/appearance", data={"csrf_token": CSRF, "saved": str(colour_id)})
    assert db.get_meta("accent_color") == "#111111"
    authed.post(f"/settings/appearance/saved/{colour_id}/delete", data={"csrf_token": CSRF})
    assert db.list_saved_colours() == []
    assert db.get_meta("accent_color") == "#111111"  # the colour in use stays


@pytest.mark.parametrize("data", [
    {"name": "", "accent": "#111111"},
    {"name": "x" * 41, "accent": "#111111"},
    {"name": "Bad", "accent": "#111111;}*{x:y"},
])
def test_save_colour_validates(authed, data):
    r = authed.post("/settings/appearance/save", data={"csrf_token": CSRF, **data}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert db.list_saved_colours() == []


def test_save_colour_needs_csrf(authed):
    r = authed.post("/settings/appearance/save",
                    data={"csrf_token": "wrong", "name": "X", "accent": "#111111"}, follow_redirects=False)
    assert r.status_code == 403 and db.list_saved_colours() == []


def test_accent_rejects_css_injection(authed):
    r = authed.post("/settings/appearance", data={"csrf_token": CSRF, "accent": "#000000;}*{display:none"},
                    follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert db.get_meta("accent_color") is None


def test_accent_reset(authed):
    db.set_meta("accent_color", "#2563eb")
    db.set_meta("accent2_color", "#f59e0b")
    authed.post("/settings/appearance", data={"csrf_token": CSRF})
    assert db.get_meta("accent_color") is None and db.get_meta("accent2_color") is None


def test_accent_requires_csrf(authed):
    r = authed.post("/settings/appearance", data={"csrf_token": "wrong", "accent": "#2563eb"})
    assert r.status_code == 403


# ---- notification services ----

def test_added_service_is_shown_masked_never_raw(authed):
    r = authed.post("/settings/notify/add", data={"csrf_token": CSRF, "url": GOTIFY, "label": "phone"},
                    follow_redirects=False)
    assert "ok=" in r.headers["location"]
    page = authed.get("/settings/notifications").text
    assert "Gotify" in page and "phone" in page
    assert TOKEN not in page
    assert all(TOKEN not in e["detail"] for e in db.list_audit())


@pytest.mark.parametrize("url", ["", "bogus://x", "not a url", "ntfy://a b", "x" * 2001])
def test_invalid_service_urls_are_refused(authed, url):
    r = authed.post("/settings/notify/add", data={"csrf_token": CSRF, "url": url}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert db.list_notify_targets() == []


def test_duplicate_service_is_refused(authed):
    authed.post("/settings/notify/add", data={"csrf_token": CSRF, "url": GOTIFY})
    r = authed.post("/settings/notify/add", data={"csrf_token": CSRF, "url": GOTIFY}, follow_redirects=False)
    assert "already" in r.headers["location"]


def test_service_limit(authed, monkeypatch):
    monkeypatch.setattr(notify, "MAX_TARGETS", 1)
    authed.post("/settings/notify/add", data={"csrf_token": CSRF, "url": GOTIFY})
    r = authed.post("/settings/notify/add", data={"csrf_token": CSRF, "url": "ntfy://ntfy.sh/other"},
                    follow_redirects=False)
    assert "up%20to%201" in r.headers["location"]


def test_remove_service(authed):
    authed.post("/settings/notify/add", data={"csrf_token": CSRF, "url": GOTIFY})
    [row] = db.list_notify_targets()
    authed.post(f"/settings/notify/{row['id']}/delete", data={"csrf_token": CSRF})
    assert db.list_notify_targets() == []


def test_env_services_are_listed_read_only(authed, monkeypatch):
    monkeypatch.setenv("APPRISE_URLS", f"ntfy://ntfy.sh/topic {GOTIFY}")
    page = authed.get("/settings/notifications").text
    assert page.count('title="Set by APPRISE_URLS') == 2
    assert "/settings/notify/env" not in page  # no delete form for them
    assert TOKEN not in page


def test_send_merges_env_and_stored_services(monkeypatch):
    monkeypatch.setenv("APPRISE_URLS", "ntfy://ntfy.sh/topic")
    db.add_notify_target(GOTIFY, None)
    sent = []
    monkeypatch.setattr(notify, "_deliver", lambda urls, title, body: sent.append(urls) or True)
    assert notify.send("staged", "t", "b")
    assert sent == [["ntfy://ntfy.sh/topic", GOTIFY]]


# ---- events ----

def test_events_default_to_all_then_follow_env_then_settings(monkeypatch):
    monkeypatch.delenv("NOTIFY_EVENTS", raising=False)
    assert notify.enabled_events() == set(notify.EVENTS)
    monkeypatch.setenv("NOTIFY_EVENTS", "rejected")
    assert notify.enabled_events() == {"rejected"}
    db.set_meta("notify_events", "staged,install_failed")
    assert notify.enabled_events() == {"staged", "install_failed"}


def test_saving_no_events_turns_them_all_off(authed):
    authed.post("/settings/notify/events", data={"csrf_token": CSRF})
    assert notify.enabled_events() == set()


def test_saving_events_ignores_unknown_names(authed):
    authed.post("/settings/notify/events", data={"csrf_token": CSRF, "events": ["staged", "bogus"]})
    assert db.get_meta("notify_events") == "staged"


# ---- test sends ----

def test_test_one_service(authed, monkeypatch):
    authed.post("/settings/notify/add", data={"csrf_token": CSRF, "url": GOTIFY})
    [row] = db.list_notify_targets()
    seen = []
    monkeypatch.setattr(notify, "_deliver", lambda urls, title, body: seen.append(urls) or True)
    r = authed.post("/settings/notify/test", data={"csrf_token": CSRF, "target": str(row["id"])},
                    follow_redirects=False)
    assert "ok=" in r.headers["location"] and "Delivered" in r.headers["location"]
    assert seen == [[GOTIFY]]


def test_test_all_reports_failures(authed, monkeypatch):
    monkeypatch.setenv("APPRISE_URLS", "ntfy://ntfy.sh/topic")
    authed.post("/settings/notify/add", data={"csrf_token": CSRF, "url": GOTIFY})
    monkeypatch.setattr(notify, "_deliver", lambda urls, title, body: "ntfy" in urls[0])
    r = authed.post("/settings/notify/test", data={"csrf_token": CSRF, "target": "all"}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert "ntfy" in r.headers["location"] and "Gotify" in r.headers["location"]


def test_test_env_service_by_index(authed, monkeypatch):
    monkeypatch.setenv("APPRISE_URLS", "ntfy://ntfy.sh/topic")
    monkeypatch.setattr(notify, "_deliver", lambda urls, title, body: True)
    r = authed.post("/settings/notify/test", data={"csrf_token": CSRF, "target": "env:0"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    assert authed.post("/settings/notify/test", data={"csrf_token": CSRF, "target": "env:5"}).status_code == 404


def test_test_with_nothing_configured(authed, monkeypatch):
    monkeypatch.delenv("APPRISE_URLS", raising=False)
    r = authed.post("/settings/notify/test", data={"csrf_token": CSRF, "target": "all"}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_settings_requires_login(client):
    assert client.get("/settings", follow_redirects=False).status_code == 303


# ---- Apprise API server ----

@pytest.mark.parametrize("server,key,tags,expected", [
    ("http://apprise.local:8000/notify/my-key", "", "", "apprise://apprise.local:8000/my-key"),
    ("apprise.local:8000", "my-key", "", "apprise://apprise.local:8000/my-key"),
    ("https://apprise.example.com/", "my-key", "phone, desk", "apprises://apprise.example.com/my-key?tags=phone,desk"),
    ("https://example.com/apprise/notify/my-key", "", "", "apprises://example.com/apprise/my-key"),
    ("http://apprise.local:8000/notify/my-key", "other", "", "apprise://apprise.local:8000/other"),
])
def test_build_api_url(server, key, tags, expected):
    assert notify.build_api_url(server, key, tags) == expected


@pytest.mark.parametrize("server,key,tags", [
    ("", "my-key", ""), ("ftp://host", "my-key", ""), ("http://host:1", "", ""),
    ("http://user:pw@host/notify/my-key", "", ""), ("http://host/notify/my-key?tags=x", "", ""),
    ("http://host", "bad key!", ""), ("http://host", "my-key", "ok, not ok!"),
    ("http://host", "my-key", ",".join(f"t{i}" for i in range(11))),
])
def test_build_api_url_refuses(server, key, tags):
    with pytest.raises(ValueError):
        notify.build_api_url(server, key, tags)


def test_add_apprise_server_from_its_notify_url(authed):
    r = authed.post("/settings/notify/add-server",
                    data={"csrf_token": CSRF, "server": "http://apprise.local:8000/notify/my-key", "label": "home"},
                    follow_redirects=False)
    assert "ok=" in r.headers["location"] and "Apprise%20API" in r.headers["location"]
    [row] = db.list_notify_targets()
    assert row["url"] == "apprise://apprise.local:8000/my-key" and row["label"] == "home"
    page = authed.get("/settings/notifications").text
    assert "Apprise API" in page and "apprise.local:8000" in page


def test_add_apprise_server_error_is_reported(authed):
    r = authed.post("/settings/notify/add-server", data={"csrf_token": CSRF, "server": "http://host"},
                    follow_redirects=False)
    assert "config%20key" in r.headers["location"]
    assert db.list_notify_targets() == []


# ---- test before saving ----

def test_try_server_sends_without_saving_and_keeps_the_form(authed, monkeypatch):
    seen = []
    monkeypatch.setattr(notify, "_deliver", lambda urls, title, body: seen.append(urls) or True)
    r = authed.post("/settings/notify/try-server",
                    data={"csrf_token": CSRF, "server": "http://apprise.local:8000/notify/my-key", "tags": "phone"})
    assert r.status_code == 200
    assert seen == [["apprise://apprise.local:8000/my-key?tags=phone"]]
    assert db.list_notify_targets() == []
    assert 'value="http://apprise.local:8000/notify/my-key"' in r.text and 'value="phone"' in r.text
    assert "Press Add to keep it" in r.text


def test_try_url_failure_is_shown(authed, monkeypatch):
    monkeypatch.setattr(notify, "_deliver", lambda urls, title, body: False)
    r = authed.post("/settings/notify/try", data={"csrf_token": CSRF, "url": GOTIFY})
    assert "Test failed" in r.text and db.list_notify_targets() == []
    assert f'value="{GOTIFY}"' in r.text  # the operator's own draft, back in their form


def test_try_invalid_input_is_explained_without_sending(authed, monkeypatch):
    monkeypatch.setattr(notify, "_deliver", lambda *a: pytest.fail("should not send"))
    r = authed.post("/settings/notify/try-server", data={"csrf_token": CSRF, "server": "http://host"})
    assert "config key" in r.text


def test_try_requires_csrf(authed):
    assert authed.post("/settings/notify/try", data={"csrf_token": "x", "url": GOTIFY}).status_code == 403


def test_forms_have_test_buttons(authed):
    page = authed.get("/settings/notifications").text
    assert 'formaction="/settings/notify/try-server"' in page
    assert 'formaction="/settings/notify/try"' in page
