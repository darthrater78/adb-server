"""The app-wide accent colour: one colour for everything that highlights
(solid buttons, links, focus rings, the current page in the nav, the chosen
filter or Settings tab, checkboxes, the edge of an open card). Status badges
keep their own fixed colours: they carry meaning. Pure functions, no I/O.

The colour has to work as text and as a button fill on the light and the
dark backgrounds, so it's nudged darker for the light theme and lighter for
the dark ones until it meets WCAG AA contrast (4.5:1), and the text drawn on
it is whichever of white or near-black reads better."""
import re

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# Name -> colour. The default (nothing saved) is style.css's own palette,
# which is "teal".
PRESETS = {
    "teal": "#0f766e",
    "blue": "#1d4ed8",
    "indigo": "#4338ca",
    "violet": "#6d28d9",
    "rose": "#be123c",
    "orange": "#c2410c",
    "green": "#15803d",
    "graphite": "#52525b",
}
# Hand-picked dark-theme shades for the presets. Lightening a colour by
# mixing in white washes it out; these stay saturated. Custom colours still
# fall back to the computed variant.
PRESET_DARK = {
    "teal": "#2dd4bf",
    "blue": "#60a5fa",
    "indigo": "#818cf8",
    "violet": "#a78bfa",
    "rose": "#fb7185",
    "orange": "#fb923c",
    "green": "#4ade80",
    "graphite": "#a1a1aa",
}
DEFAULT_PRESET = "teal"
LIGHT_BGS = ("#ffffff", "#f6f5f2")  # Flashbang: cards, page
DARK_BGS = ("#1e1d1b", "#161614", "#0e0e0d", "#000000")  # Dark and OLED: cards, page
MIN_CONTRAST = 4.5
_DARK_TEXT = "#0b1412"


def normalize(value: str) -> str:
    """Returns a lowercase #rrggbb or raises ValueError."""
    value = (value or "").strip()
    if not HEX_RE.match(value):
        raise ValueError("Pick a colour as #rrggbb")
    return value.lower()


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _mix(hex_color: str, toward: str, amount: float) -> str:
    a, b = _rgb(hex_color), _rgb(toward)
    return _hex(x + (y - x) * amount for x, y in zip(a, b))


def _fit(color: str, backgrounds, toward: str) -> str:
    """Step `color` toward black or white until it contrasts with every bg."""
    for _ in range(40):
        if all(contrast(color, bg) >= MIN_CONTRAST for bg in backgrounds):
            return color
        color = _mix(color, toward, 0.06)
    return color


def _on(color: str) -> str:
    return "#ffffff" if contrast("#ffffff", color) >= contrast(_DARK_TEXT, color) else _DARK_TEXT


def variants(color: str) -> dict[str, str]:
    color = normalize(color)
    light = _fit(color, LIGHT_BGS, "#000000")
    dark = _fit(color, DARK_BGS, "#ffffff")
    return {"light": light, "on_light": _on(light), "dark": dark, "on_dark": _on(dark)}


def preset_name(color: str | None) -> str | None:
    """Which preset the saved colour is, if any (the default when nothing is saved)."""
    if not color:
        return DEFAULT_PRESET
    for name, preset in PRESETS.items():
        if preset == color:
            return name
    return None


def swatches(saved) -> str:
    """Swatches for saved colours, like the presets' in style.css. Here
    because the CSP forbids inline styles. The colours were normalized to
    #rrggbb before they were stored."""
    return "".join(
        f".swatch-saved-{int(s['id'])} {{ background: {normalize(s['primary_color'])}; }}\n"
        for s in saved
    )


def stylesheet(color: str | None, saved=()) -> str:
    """CSS overriding style.css's --accent and --link (nothing, for the
    default), plus the saved colours' swatches. Loaded after style.css, so
    equal selectors win. The accent fills solid buttons; --link is the same
    colour, fitted to read as text on every background of the theme."""
    if not color:
        return "/* default accent */\n" + swatches(saved)
    v = variants(color)
    name = preset_name(normalize(color))
    if name in PRESET_DARK:
        v["dark"] = PRESET_DARK[name]
        v["on_dark"] = _on(v["dark"])
    light = f"--accent: {v['light']}; --on-accent: {v['on_light']}; --link: {v['light']};"
    dark = f"--accent: {v['dark']}; --on-accent: {v['on_dark']}; --link: {v['dark']};"
    return (
        f"/* accent {normalize(color)} */\n"
        f":root {{ {light} }}\n"
        f"@media (prefers-color-scheme: dark) {{ :root {{ {dark} }} }}\n"
        f':root[data-theme="flashbang"] {{ {light} }}\n'
        f':root[data-theme="dark"], :root[data-theme="oled"] {{ {dark} }}\n'
    ) + swatches(saved)
