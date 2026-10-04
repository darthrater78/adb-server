---
version: alpha
name: ADB Server
description: Self-hosted APK pusher. Tokens mirror the custom properties in app/static/style.css.
colors:
  # Flashbang (light). The accent can be replaced in Settings → Appearance.
  primary: "#0f766e"
  on-primary: "#ffffff"
  bg: "#f6f5f2"
  surface: "#ffffff"
  surface-2: "#f0efeb"
  on-surface: "#1c1b19"
  on-surface-soft: "#3d3b37"
  muted: "#6b6a66"
  border: "#e4e2dc"
  line: "#efede8"
  control-bg: "#ffffff"
  control-border: "#918e87"
  link: "#0f766e"
  ok: "#15803d"
  danger: "#b42318"
  warn: "#92400e"
  chip-rel-bg: "#e8f3ea"
  chip-rel-fg: "#166534"
  chip-art-bg: "#e7eefb"
  chip-art-fg: "#1d4ed8"
  chip-neutral-bg: "#f1efe9"
  chip-neutral-fg: "#57534e"
  chip-debug-bg: "#fdf1dc"
  chip-debug-fg: "#92400e"
  chip-error-bg: "#fdecea"
  chip-error-fg: "#b42318"
  # Dark
  bg-dark: "#161614"
  surface-dark: "#1e1d1b"
  surface-2-dark: "#2b2a26"
  on-surface-dark: "#ecebe6"
  on-surface-soft-dark: "#d9d7d0"
  muted-dark: "#a8a59d"
  border-dark: "#2e2c28"
  line-dark: "#282723"
  control-bg-dark: "#252420"
  control-border-dark: "#74716b"
  link-dark: "#5cc2b6"
  ok-dark: "#86d99a"
  danger-dark: "#f29a8a"
  warn-dark: "#f1c373"
  chip-rel-bg-dark: "#183322"
  chip-rel-fg-dark: "#86d99a"
  chip-art-bg-dark: "#1c2740"
  chip-art-fg-dark: "#9dbaf6"
  chip-neutral-bg-dark: "#2b2a26"
  chip-neutral-fg-dark: "#bdbab2"
  chip-debug-bg-dark: "#3a2c12"
  chip-debug-fg-dark: "#f1c373"
  chip-error-bg-dark: "#3b1a17"
  chip-error-fg-dark: "#f29a8a"
  # OLED: the dark palette on black; only these differ.
  bg-oled: "#000000"
  surface-oled: "#0e0e0d"
  surface-2-oled: "#1f1e1b"
  border-oled: "#252420"
  line-oled: "#1c1b19"
  control-bg-oled: "#141412"
  control-border-oled: "#6a6761"
  muted-oled: "#9c9991"
typography:
  h1: { fontFamily: Figtree, fontSize: 28px, fontWeight: 700, lineHeight: 1.2, letterSpacing: -0.01em }
  h1-phone: { fontFamily: Figtree, fontSize: 26px, fontWeight: 700, lineHeight: 1.2 }
  h2: { fontFamily: Figtree, fontSize: 17px, fontWeight: 700, lineHeight: 1.45 }
  h3: { fontFamily: Figtree, fontSize: 15px, fontWeight: 700, lineHeight: 1.45 }
  body: { fontFamily: Figtree, fontSize: 15px, fontWeight: 400, lineHeight: 1.45 }
  body-phone: { fontFamily: Figtree, fontSize: 16px, fontWeight: 400, lineHeight: 1.45 }
  body-sm: { fontFamily: Figtree, fontSize: 13px, fontWeight: 400, lineHeight: 1.45 }
  label: { fontFamily: Figtree, fontSize: 13px, fontWeight: 600, lineHeight: 1.45 }
  button: { fontFamily: Figtree, fontSize: 14px, fontWeight: 600, lineHeight: 1 }
  chip: { fontFamily: Figtree, fontSize: 12px, fontWeight: 500, lineHeight: 1 }
  tab: { fontFamily: Figtree, fontSize: 11px, fontWeight: 500, lineHeight: 1 }
  mono: { fontFamily: JetBrains Mono, fontSize: 13.5px, fontWeight: 400, lineHeight: 1.45 }
  mono-sm: { fontFamily: JetBrains Mono, fontSize: 12px, fontWeight: 400, lineHeight: 1.45 }
rounded:
  sm: 8px
  md: 10px
  control: 9px
  row: 12px
  lg: 14px
  dialog: 16px
  chip: 11px
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
  tabbar-h: 60px
components:
  button-primary: { backgroundColor: "{colors.primary}", textColor: "{colors.on-primary}", rounded: "{rounded.control}", height: 36px, padding: 16px, typography: "{typography.button}" }
  button-primary-dark: { backgroundColor: "{colors.primary}", textColor: "{colors.on-primary}" }
  button-secondary: { backgroundColor: "{colors.control-bg}", textColor: "{colors.on-surface}", rounded: "{rounded.control}" }
  button-secondary-dark: { backgroundColor: "{colors.control-bg-dark}", textColor: "{colors.on-surface-dark}" }
  button-secondary-oled: { backgroundColor: "{colors.control-bg-oled}", textColor: "{colors.on-surface-dark}" }
  button-danger: { backgroundColor: "{colors.control-bg}", textColor: "{colors.danger}", rounded: "{rounded.control}" }
  button-danger-dark: { backgroundColor: "{colors.control-bg-dark}", textColor: "{colors.danger-dark}" }
  button-danger-oled: { backgroundColor: "{colors.control-bg-oled}", textColor: "{colors.danger-dark}" }
  input: { backgroundColor: "{colors.control-bg}", textColor: "{colors.on-surface}", rounded: "{rounded.control}", height: 36px, padding: 11px }
  input-dark: { backgroundColor: "{colors.control-bg-dark}", textColor: "{colors.on-surface-dark}" }
  input-oled: { backgroundColor: "{colors.control-bg-oled}", textColor: "{colors.on-surface-dark}" }
  card: { backgroundColor: "{colors.surface}", textColor: "{colors.on-surface}", rounded: "{rounded.lg}", padding: 20px }
  card-dark: { backgroundColor: "{colors.surface-dark}", textColor: "{colors.on-surface-dark}" }
  card-oled: { backgroundColor: "{colors.surface-oled}", textColor: "{colors.on-surface-dark}" }
  page: { backgroundColor: "{colors.bg}", textColor: "{colors.on-surface}" }
  page-dark: { backgroundColor: "{colors.bg-dark}", textColor: "{colors.on-surface-dark}" }
  page-oled: { backgroundColor: "{colors.bg-oled}", textColor: "{colors.on-surface-dark}" }
  text-muted: { backgroundColor: "{colors.bg}", textColor: "{colors.muted}" }
  text-muted-on-fill: { backgroundColor: "{colors.surface-2}", textColor: "{colors.muted}" }
  text-muted-dark: { backgroundColor: "{colors.surface-2-dark}", textColor: "{colors.muted-dark}" }
  text-muted-oled: { backgroundColor: "{colors.surface-2-oled}", textColor: "{colors.muted-oled}" }
  link: { backgroundColor: "{colors.surface-2}", textColor: "{colors.link}" }
  link-dark: { backgroundColor: "{colors.surface-2-dark}", textColor: "{colors.link-dark}" }
  chip-release: { backgroundColor: "{colors.chip-rel-bg}", textColor: "{colors.chip-rel-fg}", rounded: "{rounded.chip}", height: 22px, padding: 9px }
  chip-release-dark: { backgroundColor: "{colors.chip-rel-bg-dark}", textColor: "{colors.chip-rel-fg-dark}" }
  chip-test: { backgroundColor: "{colors.chip-art-bg}", textColor: "{colors.chip-art-fg}" }
  chip-test-dark: { backgroundColor: "{colors.chip-art-bg-dark}", textColor: "{colors.chip-art-fg-dark}" }
  chip-neutral: { backgroundColor: "{colors.chip-neutral-bg}", textColor: "{colors.chip-neutral-fg}" }
  chip-neutral-dark: { backgroundColor: "{colors.chip-neutral-bg-dark}", textColor: "{colors.chip-neutral-fg-dark}" }
  chip-debug: { backgroundColor: "{colors.chip-debug-bg}", textColor: "{colors.chip-debug-fg}" }
  chip-debug-dark: { backgroundColor: "{colors.chip-debug-bg-dark}", textColor: "{colors.chip-debug-fg-dark}" }
  chip-error: { backgroundColor: "{colors.chip-error-bg}", textColor: "{colors.chip-error-fg}" }
  chip-error-dark: { backgroundColor: "{colors.chip-error-bg-dark}", textColor: "{colors.chip-error-fg-dark}" }
---

# ADB Server

## Overview

A home-server admin tool one person uses to push APKs to their own phones: at a
desk, and on the phone that is itself the target. It handles serials, versions,
hashes and addresses, so exact values matter more than decoration. No client
JavaScript (CSP `script-src 'none'`): every state is a server-rendered page, a
`<details>` fold, a `:target` dialog or a meta refresh. Warm neutral surfaces,
one accent, quiet cards.

## Colors

One accent (`primary`, teal `#0f766e` by default, user-replaceable in Settings →
Appearance and adjusted there to 4.5:1). It marks the single solid button on a
row, links, focus rings, the current nav item and the leading edge of an open
card. Status colours are fixed and never follow the accent: release (green),
test build (blue), neutral, debug (amber), error (red), each a tinted chip pair.
Three themes: Flashbang (light), Dark, OLED (Dark on `#000000`); no cookie means
follow the system.

## Typography

Figtree for all text, JetBrains Mono for versions, package names, serials,
addresses and hashes; both self-hosted (the CSP allows no other origin). Body
15px desktop, 16px on phones (16px also stops iOS zooming a focused field).
Weights: 400 body, 500 chips, 600 labels and buttons, 700 headings. No
uppercase labels, no letter-spacing except `h1` −0.01em.

## Layout

Desktop: one column, max 1200px, 40px side padding, nav in the top bar.
At ≤960px: 16px side padding, controls 44px tall, tables become stacked cards,
and the nav is a fixed bottom tab bar (60px plus the safe-area inset) with an
icon and an 11px label per tab. The phone top bar holds only the logo, the
app's name and Log out; the theme switcher is on Settings → Appearance. Spacing steps 4, 8, 12, 16, 20px. No page may
scroll sideways at any width. A form that is filled in while another app shares
the screen (pairing) keeps its fields and submit button within one 400px-tall
view.

## Elevation & Depth

Depth is tone and a 1px border: page `bg`, cards `surface`, quiet fills
`surface-2`. One shadow in light themes, `0 1px 2px` at 4% on cards; none in
dark. Floating layers only (confirm dialog, the device menu) carry a real
shadow.

## Shapes

Cards 14px; rows and inner panels 12px; controls (buttons, inputs) 9px, small
buttons 8px; flashes and inline notes 10px; dialogs and the sign-in card 16px;
chips 11px on a 22px height; avatars, swatches, filter pills and the phone
tab's icon pill are full circles or 999px. Nothing above 16px except full
pills. Every radius is a `--radius-*` custom property.

## Components

- **Buttons:** one solid `button-primary` per row; everything else is
  `button-secondary` (outlined on `control-bg`). Destructive actions are
  outlined with `danger` text, never solid red. Hover: primary brightens 8%,
  outlined ones take a `muted` (or `danger`) border. No button wraps its text.
- **Cards:** `<details>` folds with the heading in the summary and a CSS
  chevron. A card starts open only when it wants the user (an update, an
  untrusted device, a running push, the only card, or the card an action
  returned to). Add forms start folded, empty page or not.
- **Action results** show inside the card the action was taken in, not at the
  top of the page.
- **Inputs:** 36px (44px on phones), label above in `label`. Numeric values get
  `inputmode="numeric"`.
- **Chips:** 22px, tinted pair by kind; meaning never rests on colour alone,
  the chip always carries its word.
- **Confirm dialog:** every remove or delete, naming what goes and what stays.
- **Icons:** inline SVG, 1.8px stroke, `currentColor`.

## Do's and Don'ts

- Do keep exactly one solid button per row or card face.
- Do set identifiers (versions, serials, addresses) in JetBrains Mono.
- Do take every colour from a token; components hold no hex or `rgb()`.
- Don't use gradients, `backdrop-filter`, glows or emoji as icons.
- Don't add a radius outside the Shapes list.
- Don't rely on JavaScript, a third-party font or any other origin.
- Don't add all-caps labels, numbered eyebrows or stat-tile rows.
- Don't let text drop below 4.5:1 or a control's edge (`control-border` on
  `control-bg`) below 3:1 in any theme.
