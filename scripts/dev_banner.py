"""Render the README's dev-build banner from the repository's GitHub releases.

The README on main embeds banner.svg from the unprotected `readme-banner`
branch, which .github/workflows/dev-banner.yml rewrites after every Release
run. When a pre-release is newer than the newest stable release, the banner
names it, gives the first paragraph of its notes and how to run it; otherwise
it is an empty 1x1 image, so the README shows nothing.

Usage:
  gh api repos/OWNER/REPO/releases --paginate | python3 scripts/dev_banner.py > banner.svg
"""
from __future__ import annotations

import json
import re
import sys
import textwrap
from html import escape

VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-(dev|alpha|beta|rc)\.(\d+))?$")
PRE_RANK = {"dev": 0, "alpha": 1, "beta": 2, "rc": 3}
EMPTY = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>\n'

# The app's Dark theme (DESIGN.md): page, card, text, muted text, link.
BG, SURFACE, TEXT, MUTED, ACCENT = "#161614", "#1e1d1b", "#ecebe6", "#a8a59d", "#5cc2b6"
# An image on GitHub can't load the app's own fonts, so the nearest system ones.
SANS = "system-ui,-apple-system,'Segoe UI',sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
WIDTH, WRAP = 820, 100


def version_key(tag: str) -> tuple[int, ...] | None:
    """Sort key where 3.9.0-dev.2 < 3.9.0-rc.1 < 3.9.0; None for tags we don't recognise."""
    m = VERSION_RE.match(tag)
    if not m:
        return None
    major, minor, patch, pre, num = m.groups()
    if pre is None:
        return (int(major), int(minor), int(patch), 1, 0, 0)
    return (int(major), int(minor), int(patch), 0, PRE_RANK[pre], int(num))


def pick_dev(releases: list[dict]) -> dict | None:
    """The newest published pre-release, if it is newer than every stable release."""
    best_pre = best_stable = None
    for rel in releases:
        if rel.get("draft"):
            continue
        key = version_key(rel.get("tag_name", ""))
        if key is None:
            continue
        if rel.get("prerelease"):
            if best_pre is None or key > best_pre[0]:
                best_pre = (key, rel)
        elif best_stable is None or key > best_stable[0]:
            best_stable = (key, rel)
    if best_pre is None or (best_stable is not None and best_stable[0] > best_pre[0]):
        return None
    return best_pre[1]


def summary(body: str) -> str:
    """The release notes' opening paragraph as plain text."""
    first = (body or "").strip().replace("\r\n", "\n").split("\n\n", 1)[0]
    if not first or first.startswith("#"):
        return ""
    text = re.sub(r"\*\*|__|`", "", " ".join(first.split()))
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text).strip()


def render(release: dict | None) -> str:
    if release is None:
        return EMPTY
    tag = release["tag_name"]
    version = tag.removeprefix("v")
    lines = textwrap.wrap(summary(release.get("body") or ""), WRAP)
    if len(lines) > 3:
        lines = lines[:3]
        lines[2] = lines[2][: WRAP - 1].rstrip() + "…"

    y = 36
    parts = [
        f'<text x="28" y="{y}" font-family="{SANS}" font-size="15" font-weight="700" fill="{ACCENT}">'
        f"Dev build available: {escape(tag)}"
        f'<tspan font-weight="400" fill="{MUTED}"> · for testing, not for production</tspan></text>'
    ]
    for line in lines:
        y += 23
        parts.append(f'<text x="28" y="{y}" font-family="{SANS}" font-size="14" fill="{TEXT}">{escape(line)}</text>')
    y += 16
    parts.append(f'<rect x="28" y="{y}" width="{WIDTH - 56}" height="32" rx="9" fill="{BG}"/>')
    parts.append(
        f'<text x="42" y="{y + 21}" font-family="{MONO}" font-size="13" fill="{MUTED}">'
        f'<tspan fill="{TEXT}">both image tags to {escape(version)}</tspan>, then '
        f'<tspan fill="{TEXT}">docker compose pull &amp;&amp; docker compose up -d</tspan></text>'
    )
    height = y + 32 + 22
    label = escape(f"Dev build {tag} available: set both image tags in compose.yaml to {version}")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{label}">\n'
        f"<title>{label}</title>\n"
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="14" fill="{SURFACE}" stroke="#2e2c28"/>\n'
        + "\n".join(parts)
        + "\n</svg>\n"
    )


def parse_pages(data: str) -> list[dict]:
    """`gh api --paginate` prints one JSON array per page, back to back."""
    releases: list[dict] = []
    decoder, pos = json.JSONDecoder(), 0
    while pos < len(data):
        if data[pos].isspace():
            pos += 1
            continue
        page, pos = decoder.raw_decode(data, pos)
        releases.extend(page)
    return releases


def main() -> int:
    sys.stdout.write(render(pick_dev(parse_pages(sys.stdin.read()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
