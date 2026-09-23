"""The app-wide accent colours: a primary (solid buttons, the active nav
item) and a secondary (links, tags, outlined buttons, focus rings). Pure
functions, no I/O.

Each colour has to work as text and as a button fill on both
white and near-black backgrounds, so it's nudged darker for the light themes
and lighter for the dark ones until it meets WCAG AA contrast (4.5:1), and the
text drawn on it is whichever of white or near-black reads better."""
import re

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# Name -> (primary, secondary) for the presets: a primary with a quieter,
# related secondary, not complementary opposites. The default (nothing saved)
# is the palette in style.css, which is "teal-ocean".
PRESETS = {
    "teal-ocean": ("#0f766e", "#0369a1"),
    "graphite-blue": ("#3f3f46", "#2563eb"),
    "navy-teal": ("#1e3a8a", "#0d9488"),
    "indigo-cyan": ("#4338ca", "#0e7490"),
    "forest-ochre": ("#166534", "#a16207"),
    "plum-slate": ("#6b21a8", "#64748b"),
    "terracotta-stone": ("#9a3412", "#78716c"),
}
# Hand-picked dark-theme shades for the presets. Lightening a colour by
# mixing in white washes it out; these stay saturated. Custom colours still
# fall back to the computed variant.
PRESET_DARK = {
    "teal-ocean": ("#2dd4bf", "#38bdf8"),
    "graphite-blue": ("#a1a1aa", "#60a5fa"),
    "navy-teal": ("#93c5fd", "#2dd4bf"),
    "indigo-cyan": ("#818cf8", "#22d3ee"),
    "forest-ochre": ("#4ade80", "#eab308"),
    "plum-slate": ("#c084fc", "#94a3b8"),
    "terracotta-stone": ("#fb923c", "#a8a29e"),
}
DEFAULT_PRESET = "teal-ocean"
LIGHT_BG = "#ffffff"
DARK_BGS = ("#14161a", "#000000")  # Dark and OLED
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
    light = _fit(color, (LIGHT_BG,), "#000000")
    dark = _fit(color, DARK_BGS, "#ffffff")
    return {"light": light, "on_light": _on(light), "dark": dark, "on_dark": _on(dark)}


def preset_name(primary: str | None, secondary: str | None) -> str | None:
    """Which preset a saved pair is, if any (the default when nothing is saved)."""
    if not primary:
        return DEFAULT_PRESET
    for name, pair in PRESETS.items():
        if pair == (primary, secondary):
            return name
    return None


def swatches(saved) -> str:
    """Split swatches for saved pairs, like the presets' in style.css. Here
    because the CSP forbids inline styles. The colours were normalized
    to #rrggbb before they were stored."""
    return "".join(
        f".swatch-saved-{int(s['id'])} {{ background: linear-gradient(135deg, "
        f"{normalize(s['primary_color'])} 50%, {normalize(s['secondary_color'])} 50%); }}\n"
        for s in saved
    )


def stylesheet(primary: str | None, secondary: str | None = None, saved=()) -> str:
    """CSS overriding style.css's --accent/--accent-2 (nothing, for the
    default palette), plus the saved pairs' swatches. Loaded after
    style.css, so equal selectors win."""
    if not primary:
        return "/* default accent */\n" + swatches(saved)
    secondary = secondary or PRESETS[DEFAULT_PRESET][1]
    p, s2 = variants(primary), variants(secondary)
    name = preset_name(normalize(primary), normalize(secondary))
    if name in PRESET_DARK:
        for v, dark in zip((p, s2), PRESET_DARK[name]):
            v["dark"], v["on_dark"] = dark, _on(dark)
    light = (f"--accent: {p['light']}; --on-accent: {p['on_light']}; "
             f"--accent-2: {s2['light']}; --on-accent-2: {s2['on_light']};")
    dark = (f"--accent: {p['dark']}; --on-accent: {p['on_dark']}; "
            f"--accent-2: {s2['dark']}; --on-accent-2: {s2['on_dark']};")
    return (
        f"/* accent {normalize(primary)} / {normalize(secondary)} */\n"
        f":root {{ {light} }}\n"
        f"@media (prefers-color-scheme: dark) {{ :root {{ {dark} }} }}\n"
        f':root[data-theme="flashbang"] {{ {light} }}\n'
        f':root[data-theme="dark"], :root[data-theme="oled"] {{ {dark} }}\n'
    ) + swatches(saved)
