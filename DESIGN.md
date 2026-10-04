---
version: alpha
name: ADB Server
description: Self-hosted APK pusher. Tokens mirror the custom properties in app/static/style.css.
colors:
  # Flashbang (light). The accent can be replaced in Settings → Appearance.
  bg: "#f3f3f3"
  surface: "#ffffff"
  surface-2: "#e4e4e4"
  header-bg: "#e4e4e4"
  glow: "#e3eef3"
  on-surface: "#1a1a1a"
  on-surface-soft: "#333333"
  muted: "#5c5c5c"
  border: "#cfcfcf"
  line: "#dcdcdc"
  control-bg: "#dadada"
  control-border: "#7a7a7a"
  primary: "#0099cc"
  on-primary: "#001e2b"
  link: "#00719a"
  ok: "#456e00"
  danger: "#ad1d16"
  warn: "#8a5a00"
  tag-rel: "#3d6100"
  tag-art: "#00607f"
  tag-neutral: "#4a4a4a"
  tag-debug: "#7a4f00"
  tag-error: "#a81d17"
  flash-ok-bg: "#e6efd6"
  flash-warn-bg: "#f6e7c4"
  flash-error-bg: "#f7dcd9"
  # Dark
  bg-dark: "#0f0f0f"
  surface-dark: "#1c1c1c"
  surface-2-dark: "#262626"
  header-bg-dark: "#1c1c1c"
  glow-dark: "#14303c"
  on-surface-dark: "#f2f2f2"
  on-surface-soft-dark: "#dcdcdc"
  muted-dark: "#a3a3a3"
  border-dark: "#303030"
  line-dark: "#262626"
  control-bg-dark: "#2e2e2e"
  control-border-dark: "#7d7d7d"
  primary-dark: "#33b5e5"
  on-primary-dark: "#001e2b"
  link-dark: "#33b5e5"
  ok-dark: "#99cc00"
  danger-dark: "#ff6b6b"
  warn-dark: "#ffbb33"
  tag-rel-dark: "#b4dc4a"
  tag-art-dark: "#6ccdf0"
  tag-neutral-dark: "#bdbdbd"
  tag-debug-dark: "#ffbb33"
  tag-error-dark: "#ff8a80"
  flash-ok-bg-dark: "#1f2a0a"
  flash-warn-bg-dark: "#33260a"
  flash-error-bg-dark: "#3a1414"
  # OLED: the dark palette on black; only these differ.
  bg-oled: "#000000"
  surface-oled: "#121212"
  surface-2-oled: "#1c1c1c"
  header-bg-oled: "#000000"
  glow-oled: "#000000"
  border-oled: "#262626"
  line-oled: "#1c1c1c"
  control-bg-oled: "#242424"
  control-border-oled: "#757575"
  muted-oled: "#9e9e9e"
typography:
  wordmark: { fontFamily: Roboto, fontSize: 112px, fontWeight: 100, lineHeight: 1, letterSpacing: -0.03em }
  wordmark-phone: { fontFamily: Roboto, fontSize: 58px, fontWeight: 100, lineHeight: 1, letterSpacing: -0.03em }
  h1: { fontFamily: Roboto, fontSize: 34px, fontWeight: 300, lineHeight: 1.15, letterSpacing: -0.01em }
  h1-phone: { fontFamily: Roboto, fontSize: 28px, fontWeight: 300, lineHeight: 1.15 }
  numeral: { fontFamily: Roboto, fontSize: 28px, fontWeight: 300, lineHeight: 1, letterSpacing: -0.01em }
  numeral-sm: { fontFamily: Roboto, fontSize: 22px, fontWeight: 300, lineHeight: 1 }
  section: { fontFamily: Roboto, fontSize: 16px, fontWeight: 700, lineHeight: 1.45, letterSpacing: 0.06em }
  group: { fontFamily: Roboto, fontSize: 24px, fontWeight: 300, lineHeight: 1.2 }
  label: { fontFamily: Roboto, fontSize: 12px, fontWeight: 700, lineHeight: 1.45, letterSpacing: 0.06em }
  tag: { fontFamily: Roboto, fontSize: 11px, fontWeight: 700, lineHeight: 1, letterSpacing: 0.06em }
  row: { fontFamily: Roboto, fontSize: 17px, fontWeight: 400, lineHeight: 1.45 }
  body: { fontFamily: Roboto, fontSize: 15px, fontWeight: 400, lineHeight: 1.45 }
  body-phone: { fontFamily: Roboto, fontSize: 16px, fontWeight: 400, lineHeight: 1.45 }
  body-sm: { fontFamily: Roboto, fontSize: 13px, fontWeight: 400, lineHeight: 1.45 }
  button: { fontFamily: Roboto, fontSize: 14px, fontWeight: 500, lineHeight: 1 }
  mono: { fontFamily: Roboto Mono, fontSize: 13.5px, fontWeight: 400, lineHeight: 1.45 }
  mono-sm: { fontFamily: Roboto Mono, fontSize: 12px, fontWeight: 400, lineHeight: 1.45 }
rounded:
  corner: 2px
  full: 999px
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 20px
  page-desktop: 40px
  page-phone: 16px
  page-max: 1200px
  control-h: 36px
  control-h-phone: 44px
  bar-h: 48px
components:
  page: { backgroundColor: "{colors.bg}", textColor: "{colors.on-surface}" }
  page-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.on-surface-dark}" }
  page-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.on-surface-dark}" }
  action-bar: { backgroundColor: "{colors.header-bg}", textColor: "{colors.on-surface}", height: 48px }
  action-bar-dark: { backgroundColor: "{colors.header-bg-dark}", textColor: "{colors.on-surface-dark}" }
  action-bar-oled: { backgroundColor: "{colors.header-bg-oled}", textColor: "{colors.on-surface-dark}" }
  tab: { backgroundColor: "{colors.header-bg}", textColor: "{colors.muted}", typography: "{typography.label}" }
  tab-dark: { backgroundColor: "{colors.header-bg-dark}", textColor: "{colors.muted-dark}" }
  tab-oled: { backgroundColor: "{colors.header-bg-oled}", textColor: "{colors.muted-oled}" }
  button-primary: { backgroundColor: "{colors.primary}", textColor: "{colors.on-primary}", rounded: "{rounded.corner}", height: 36px, padding: 16px, typography: "{typography.button}" }
  button-primary-dark: { backgroundColor: "{colors.primary-dark}", textColor: "{colors.on-primary-dark}" }
  button-secondary: { backgroundColor: "{colors.control-bg}", textColor: "{colors.on-surface}", rounded: "{rounded.corner}" }
  button-secondary-dark: { backgroundColor: "{colors.control-bg-dark}", textColor: "{colors.on-surface-dark}" }
  button-secondary-oled: { backgroundColor: "{colors.control-bg-oled}", textColor: "{colors.on-surface-dark}" }
  button-danger: { backgroundColor: "{colors.control-bg}", textColor: "{colors.danger}", rounded: "{rounded.corner}" }
  button-danger-dark: { backgroundColor: "{colors.control-bg-dark}", textColor: "{colors.danger-dark}" }
  button-danger-oled: { backgroundColor: "{colors.control-bg-oled}", textColor: "{colors.danger-dark}" }
  switch-off: { backgroundColor: "{colors.muted}", textColor: "{colors.bg}", rounded: "{rounded.corner}" }
  switch-off-dark: { backgroundColor: "{colors.muted-dark}", textColor: "{colors.bg-dark}" }
  switch-off-oled: { backgroundColor: "{colors.muted-oled}", textColor: "{colors.bg-oled}" }
  switch-on: { backgroundColor: "{colors.primary}", textColor: "{colors.on-primary}", rounded: "{rounded.corner}" }
  switch-on-dark: { backgroundColor: "{colors.primary-dark}", textColor: "{colors.on-primary-dark}" }
  input: { backgroundColor: "{colors.bg}", textColor: "{colors.on-surface}", height: 36px, padding: 6px }
  input-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.on-surface-dark}" }
  input-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.on-surface-dark}" }
  dialog: { backgroundColor: "{colors.surface}", textColor: "{colors.on-surface}", rounded: "{rounded.corner}" }
  dialog-dark: { backgroundColor: "{colors.surface-dark}", textColor: "{colors.on-surface-dark}" }
  dialog-oled: { backgroundColor: "{colors.surface-oled}", textColor: "{colors.on-surface-dark}" }
  dialog-title: { backgroundColor: "{colors.surface}", textColor: "{colors.link}" }
  dialog-title-dark: { backgroundColor: "{colors.surface-dark}", textColor: "{colors.link-dark}" }
  dialog-title-oled: { backgroundColor: "{colors.surface-oled}", textColor: "{colors.link-dark}" }
  text-muted: { backgroundColor: "{colors.bg}", textColor: "{colors.muted}" }
  text-muted-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.muted-dark}" }
  text-muted-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.muted-oled}" }
  text-muted-on-fill: { backgroundColor: "{colors.surface-2}", textColor: "{colors.muted}" }
  text-muted-on-fill-dark: { backgroundColor: "{colors.surface-2-dark}", textColor: "{colors.muted-dark}" }
  text-muted-on-fill-oled: { backgroundColor: "{colors.surface-2-oled}", textColor: "{colors.muted-oled}" }
  link: { backgroundColor: "{colors.bg}", textColor: "{colors.link}" }
  link-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.link-dark}" }
  link-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.link-dark}" }
  numeral-offer: { backgroundColor: "{colors.bg}", textColor: "{colors.link}", typography: "{typography.numeral}" }
  numeral-offer-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.link-dark}" }
  numeral-offer-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.link-dark}" }
  status-ok: { backgroundColor: "{colors.bg}", textColor: "{colors.ok}" }
  status-ok-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.ok-dark}" }
  status-ok-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.ok-dark}" }
  status-danger: { backgroundColor: "{colors.bg}", textColor: "{colors.danger}" }
  status-danger-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.danger-dark}" }
  status-danger-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.danger-dark}" }
  status-warn: { backgroundColor: "{colors.bg}", textColor: "{colors.warn}" }
  status-warn-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.warn-dark}" }
  status-warn-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.warn-dark}" }
  tag-rel: { backgroundColor: "{colors.bg}", textColor: "{colors.tag-rel}", rounded: "{rounded.corner}", height: 18px, padding: 6px, typography: "{typography.tag}" }
  tag-rel-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.tag-rel-dark}" }
  tag-rel-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.tag-rel-dark}" }
  tag-art: { backgroundColor: "{colors.bg}", textColor: "{colors.tag-art}" }
  tag-art-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.tag-art-dark}" }
  tag-art-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.tag-art-dark}" }
  tag-neutral: { backgroundColor: "{colors.bg}", textColor: "{colors.tag-neutral}" }
  tag-neutral-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.tag-neutral-dark}" }
  tag-neutral-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.tag-neutral-dark}" }
  tag-debug: { backgroundColor: "{colors.bg}", textColor: "{colors.tag-debug}" }
  tag-debug-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.tag-debug-dark}" }
  tag-debug-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.tag-debug-dark}" }
  tag-error: { backgroundColor: "{colors.bg}", textColor: "{colors.tag-error}" }
  tag-error-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.tag-error-dark}" }
  tag-error-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.tag-error-dark}" }
  flash-ok: { backgroundColor: "{colors.flash-ok-bg}", textColor: "{colors.tag-rel}" }
  flash-ok-dark: { backgroundColor: "{colors.flash-ok-bg-dark}", textColor: "{colors.tag-rel-dark}" }
  flash-warn: { backgroundColor: "{colors.flash-warn-bg}", textColor: "{colors.tag-debug}" }
  flash-warn-dark: { backgroundColor: "{colors.flash-warn-bg-dark}", textColor: "{colors.tag-debug-dark}" }
  flash-error: { backgroundColor: "{colors.flash-error-bg}", textColor: "{colors.tag-error}" }
  flash-error-dark: { backgroundColor: "{colors.flash-error-bg-dark}", textColor: "{colors.tag-error-dark}" }
---

# ADB Server

## Overview

"Developer options": a home-server admin tool one person uses to push APKs to
their own phones, drawn in the Holo look of the Android settings screen where
adb is switched on. Used at a desk, and on the phone that is itself the
target. It handles serials, versions, hashes and addresses, so exact values
matter more than decoration. No client JavaScript (CSP `script-src 'none'`):
every state is a server-rendered page, a `<details>` fold, a `:target` dialog
or a meta refresh. Flat and square, one accent, sections told apart by tone.

## Colors

One accent (`primary`: Holo blue, `#0099cc` light and `#33b5e5` dark;
user-replaceable in Settings → Appearance and fitted there to 4.5:1 as text).
It marks the rule under the action bar, the current tab, the one solid button
on a row, a focused field's bracket, an ON switch, a dialog's title and the
version on offer. `link` is the accent as text. Status colours are fixed and
never follow the accent: release, test build, neutral, debug, error, each a
tag colour, with a tinted background only for flashes. Presets are Holo's own
five (blue, violet, green, orange, red) and a grey. Three themes: Flashbang
(Holo Light), Dark, OLED (Dark on `#000000`); no cookie means follow the
system.

## Typography

Roboto for all text, Roboto Mono for package names, serials, addresses and
hashes; both self-hosted (the CSP allows no other origin). Roboto's width axis
draws the condensed capitals. Three voices:

- **Thin numerals**, weight 300: page titles (34px) and the version on offer
  (28px in `link` for an update, 22px in `muted` otherwise). The sign-in
  wordmark is weight 100 beside weight 700, like the Holo lock-screen clock.
- **Condensed capitals**, weight 700, width 75%, 0.06em tracking: tabs,
  section headings, field labels, table heads, tags, the switch's ON/OFF.
- **Plain text**: body 15px (16px on phones, which also stops iOS zooming a
  focused field), a row's name 17px.

Names (a repo, an app) are never uppercased; only labels are.

## Layout

One column, max 1200px, 40px side padding. The action bar is 48px: logo, name,
tabs, then version, theme and Log out. At ≤960px: 16px side padding, controls
44px tall, tables become ruled rows, and the tabs take their own full-width
row under the bar with the accent rule between the two (no bottom bar). The
theme switcher is then only on Settings → Appearance. Spacing steps 4, 8, 12,
16, 20px. No page may scroll sideways at any width. A form that is filled in
while another app shares the screen (pairing) keeps its fields and submit
button within one 400px-tall view.

## Elevation & Depth

Depth is tone only. The page is `bg`; each section is a square `surface` panel
with 24px between panels, so where one ends and the next starts is plain. A
section's head is a `surface-2` band across its full width with its heading
at 16px in `on-surface`; open, a 2px accent rule closes the band. Rows inside are split by a 1px `line`. A group
of sections sits under a 2px `muted` rule on the page itself. No borders or
shadows on panels; only floating layers (the confirm dialog, the device menu)
carry a shadow. Every page's backdrop fades from `bg` at the top to `glow`
at the foot of the window, fixed while the page scrolls, as Holo's did; that
is the only gradient. Over it lies a faint isometric grid (`grid.svg`, the
logo's box repeated, 1px lines at 8–22% opacity by theme), strongest at the
foot and gone by the top. On OLED `glow` is black, so the page stays black.

## Shapes

One corner, 2px, on everything that has one: buttons, tags, switches, dialogs,
flashes. Fields have no box, only a bracket (a bottom rule with a 6px tick at
each end). Circles are reserved for the unlock ring and colour dots.

## Components

- **Action bar and tabs:** `header-bg`, a 2px accent rule beneath. The current
  tab has a 4px accent underline and `on-surface` text; the others are `muted`.
- **Sections:** a `<details>` fold on a `surface` panel, 20px side padding
  (16px on phones); its summary is the head band, with a CSS chevron. It starts open only when it wants the user (an update, an
  untrusted device, a running push, the only one, or the one an action
  returned to). Add forms start folded, empty page or not.
- **Views of a page:** where a page holds different kinds of thing (Apps:
  watched repos, test builds, uploads), each kind is a view behind a large
  thin tab (22px, weight 300) with its count, one view showing at a time.
  The tabs are links to each view's id; `:target` and `:has()` do the rest.
- **Buttons:** square slabs. One accent `button-primary` per row; everything
  else is a grey `button-secondary`. Destructive actions are grey with
  `danger` text, never solid red. No button wraps its text.
- **Switch:** a 76×26px slab (32px tall on phones) with a thumb that reads
  OFF in `muted` or ON in the accent. It is its form's submit button.
- **Fields:** the bracket in `control-border`; focused, 2px in the accent;
  rejected, 2px in `danger`. Label above in condensed capitals.
- **Tags:** an 18px hairline box in the kind's colour; meaning never rests on
  colour alone, the tag always carries its word.
- **Dialog:** the title in `link` at weight 300 over a 2px accent rule; the
  buttons in a bar along the bottom, split by hairlines. Every remove or
  delete goes through one, naming what goes and what stays.
- **Push state:** a 3px accent bar while it runs, green once in; a failure is
  words and a link to why, no bar.
- **Sign-in:** a lock screen. The wordmark, then adb's start-up line in mono
  (`* daemon started successfully`); a refusal replaces it with
  `error: …` in `danger`. The submit button is the unlock ring.
- **Action results** show inside the section the action was taken in.
- **Icons:** inline SVG, 1.8px stroke, `currentColor`.
- **Logo:** a box in three flat tones of Holo blue with a white arrow
  launching from it, on a `glow-dark` tile. Fixed colours: it doesn't follow
  the accent or the theme.

## Do's and Don'ts

- Do keep exactly one accent button per row or section head.
- Do set identifiers (packages, serials, addresses, hashes) in Roboto Mono.
- Do take every colour from a token; components hold no hex or `rgb()`.
- Don't give a section a border, shadow or radius; its panel is tone alone.
- Don't round anything past 2px, and don't draw pills.
- Don't add a gradient beyond the page backdrop, `backdrop-filter`, glows
  or emoji as icons.
- Don't uppercase a name or a sentence, only a label.
- Don't mark a section with a coloured edge, or a row with a letter avatar.
- Don't rely on JavaScript, a third-party font or any other origin.
- Don't let text drop below 4.5:1 in any theme. A field's bracket
  (`control-border` on `bg`) stays at 3:1 or better.
