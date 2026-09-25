"""Takes the README screenshots from a seeded, GitHub-mocked stack (run.sh).
Dark theme, 1280 wide (phone: 390 at 2x). Usage: shoot.py <base-url> <out-dir> <password> base|mfa"""
import base64
import hashlib
import hmac
import struct
import sys
import time

from playwright.sync_api import sync_playwright

URL, OUT, PASSWORD, PHASE = sys.argv[1:5]
TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # seed.py's demo secret


def totp(secret: str) -> str:
    key = base64.b32decode(secret)
    digest = hmac.new(key, struct.pack(">Q", int(time.time() // 30)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def context(browser, phone=False):
    ctx = browser.new_context(viewport={"width": 390, "height": 844} if phone else {"width": 1280, "height": 800},
                              device_scale_factor=2 if phone else 1)
    ctx.add_cookies([{"name": "theme", "value": "dark", "url": URL}])
    return ctx


def sign_in(page, shot_mfa=False):
    page.goto(URL + "/login")
    page.fill("input[name=username]", "admin")
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_load_state()
    if page.locator("input[name=code]").count():
        if shot_mfa:
            page.screenshot(path=f"{OUT}/login-mfa.png")
        page.fill("input[name=code]", totp(TOTP_SECRET))
        page.click("button[type=submit]")
        page.wait_for_load_state()


def shot(page, path, name, full=True):
    page.goto(URL + path)
    page.wait_for_load_state()
    page.screenshot(path=f"{OUT}/{name}.png", full_page=full)
    if page.evaluate("document.documentElement.scrollWidth > window.innerWidth"):
        raise SystemExit(f"{name}: the page scrolls sideways")
    print("shot", name)


with sync_playwright() as p:
    browser = p.chromium.launch()
    if PHASE == "base":
        page = context(browser).new_page()
        page.goto(URL + "/login")
        page.screenshot(path=f"{OUT}/login.png")
        sign_in(page)
        for path, name, full in (("/status", "status", True), ("/sources", "sources", True),
                                 ("/repos/1/artifacts", "builds", True), ("/install", "install", True),
                                 ("/installs/3", "install-status", False), ("/installs", "installs", False),
                                 ("/audit", "audit", True), ("/devices", "devices", True),
                                 ("/settings", "settings", False), ("/settings/github", "settings-github", True),
                                 ("/settings/notifications", "settings-notifications", True)):
            shot(page, path, name, full)
        page.goto(URL + "/sources")
        page.click("summary:has-text('Watch a GitHub repo')")  # folded once repos are watched
        page.fill("input[name=repo_url]", "example-dev/weather-app")
        page.click("text=Look up repo")
        page.wait_for_load_state()
        page.locator("#review").screenshot(path=f"{OUT}/repo-review.png")
        print("shot repo-review")
        phone = context(browser, phone=True).new_page()
        sign_in(phone)
        # One screenful, as on a phone: a full-page shot would draw the
        # fixed tab bar halfway down.
        shot(phone, "/status", "phone-status", False)
        shot(phone, "/install", "phone-install", False)
    else:
        page = context(browser).new_page()
        sign_in(page, shot_mfa=True)
        print("shot login-mfa")
        shot(page, "/settings/security", "settings-security", False)
    browser.close()
