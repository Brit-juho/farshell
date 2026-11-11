# FarShell Design System


Visual rules, tokens and component guidance for FarShell. See
[ARCHITECTURE.md](./ARCHITECTURE.md) for the module map and
[CHANGELOG.md](./CHANGELOG.md) for release history.

---

## 0. Design principles

These are the rules this project actually holds itself to. Read this before
drawing a new screen.

### Structural principles

Redesign "Workbench" (작업대) (2026-11) replaced "restraint is taste" with structure: the screen is
read from its **frame** — activity bar, sidebar, title bar 36, panes, status bar 24 — and
every new element first has to find its place in that frame before it gets a colour, a
border or a motion. Concretely:

- **One frame, fixed sizes.** Bars and rows come from the shell metrics in §2/§3 (36 · 24 ·
  48 · 44 · rows 24/28/32/36/44); a new screen reuses them instead of inventing a height.
- **Space before lines, lines before fills.** Separate with layout first, a 1px line second,
  a background step last. Never all three.
- **Active is a line, not a fill.** The current item is marked with a **2px `--color-acc`
  line** (activity bar, pane, tabs in a panel) — allowed and preferred over a filled
  background or an accent-coloured block.
- **One focal point.** Within 0.5 seconds you can name the one thing to look at.

The prohibitions stay, unchanged:

- **No emoji** as a UI asset (user content such as terminal output is exempt).
- **No icon trapped in a coloured box** (filled square/circle behind an icon). Avatars and
  app icons are the only exceptions.
- **No glow** — no blurred halo of the accent behind a button.
- **Blur only on an OS-mimicking skin's title bar** (`macos`, `windows`); every other
  surface is opaque.
- **One moving thing per screen** (§0 「Motion」).

### Color — 70/20/10

Four neutral background steps (`--color-bg-0..3`) are the 70%, four text
steps (txt/sub/muted/faint) are the 20%, and the accent is the 10%.

- **One solid accent per screen — the primary action.** If the active
  activity, a badge, and a primary button all fill with the accent at once,
  none of them reads as primary.
  → The active activity states itself with the 2px accent line (no fill); a
  selected row uses the selection tint (`--color-acc-soft`), not a solid fill.
- Don't give every element its own tinted background. Whitespace and layout
  separate things first.
- Don't add a border where a background step already separates.
- **The test:** squint at the page. If it's one blob of similar color, it failed.

### Icons

- **Never trap an icon in a filled rounded square or circle.** Avatars and app
  icons are the only exceptions.
- If the icon doesn't carry information the user needs, remove it.
- Icons do work in actions (buttons, nav) — they're not decoration on info cards.
- Don't mix libraries — there's one registry, `frontend/js/ui/icons.js`, inline SVG. Static HTML
  (`index.html`) holds only empty `<svg data-icon="name">` slots that `hydrateIcons()` fills;
  the login gate's two icons are the one exception (shown before the bundle loads, test-guarded).
- **No emoji as a UI asset.** User-generated content (terminal output, etc.) is
  the exception.

### Typography

- Sans-serif by default (`--font-ui`). Never reach for a serif as the "elegant
  default" for a hero title.
- Hierarchy comes from size, weight, and color — not stacked containers.

### Glassmorphism · gradients · shadows

- `backdrop-filter: blur()` only where a skin **explicitly mimics an OS** —
  that's `macos` (translucent header bar) and `windows` (Windows Terminal
  acrylic), and only on the title bar. The default skin `farshell`'s cards,
  panels, sidebars and activity bar are all opaque.
- No gradients on text or buttons.
- No **glow shadow** — a blurred halo of the same color behind a button.
  Shadow is gray, low-opacity, and states elevation in exactly two places:
  overlays (palette, sheets, dialogs) via `--shadow-e2`/`e3`, and **the one
  row you are currently on** via `--shadow-e1`. Every other boundary is a 1px
  line (`--color-line`).

  `--shadow-e1` must be defined in every skin, not only `notepad`. The active
  row (`.vt-wgrail-row.active`) references it; where it is undefined the
  `box-shadow` declaration is invalid and dropped, leaving the active row
  with a background step alone (1.06:1 against the rail in `farshell` —
  effectively the same colour), so "which row am I on" would be carried by the
  accent text colour and nothing else. e1 is deliberately much weaker than e2:
  e1 says "a row is lifted inside a list", e2 says "a panel floats over the
  screen".
- The exception: a 0-blur accent ring is not a shadow, it's a **focus ring** —
  an accessibility affordance, and it stays.

### Hierarchy — flatten nesting

- No cards inside cards. If a nested card is really just the parent's
  content, drop it.
- Secondary info is small and dim (`--color-sub`/`--color-muted`), not boxed.
- Every border line must be able to answer "what does this line teach the user?"

### Native controls are not used as-is

`<select>` → `ui/select.js` (auto-applied to every select; opt out with `data-native`), numeric `<input>` →
`ui/number-spinner.js`, `<input type=checkbox>` → the token-drawn checkbox in `components.css`. The dropdown list
reuses the `.vt-menu` look and lives in `screens/20-overlay.css` because `.vt-menu` is in the `legacy` layer, which beats `components` — a rule for
`.vt-dd-list` inside `@layer components` loses to it (position and z-index). Touch gets 44px rows.

### Motion

- No entrance animation (fade-in, slide-up) as a default on content.
- Never stack multiple transforms on hover.
- Animation is 120~200ms, subtle, and purposeful — feedback, state change, or
  spatial sense only (`--dur-fast`/`--dur-base`, `--ease-out-quint`).
- **Only one thing moves per screen.** Of the status dots, only `waiting`
  breathes (the state *icons* of §5 add the `working` spinner arc and the same
  `waiting` breathing — both freeze under `prefers-reduced-motion`) — if every dot pulses on a screen full of sessions (rail, fleet
  home), nothing stands out.
- Under `prefers-reduced-motion`, animation/transition is globally disabled
  and `waiting` falls back to an outline instead of a pulse.

### Pre-flight checklist

- [ ] The element has a place in the frame (bar · sidebar · pane · overlay) and uses its metrics
- [ ] Active state is the 2px accent line or the selection tint, not a solid accent fill
- [ ] No icon trapped in a color box
- [ ] No emoji used as a design element
- [ ] No default serif on a hero
- [ ] No blur outside an OS-mimicking skin's title bar
- [ ] No gradient on text or buttons
- [ ] No color glow behind a button
- [ ] Accent respects 70/20/10 (one solid accent = the primary action)
- [ ] No purposeless card nesting
- [ ] No slow entrance animation on content
- [ ] Row heights stay within 24/28/36
- [ ] No `border-left` + `border-radius` together
      (the bar gets clipped at the corner — an active line or a row status bar uses a
      dedicated `::before`/element, or `box-shadow: inset`)
- [ ] Can you name the #1 thing to look at within 0.5 seconds?

---

## 1. Skins — 9

Switched via `<html data-skin="...">`. **Every color is referenced through a
token** — no hardcoded hex (checked across `frontend/js` and `styles/layers`;
`grep -rnE "#[0-9a-f]{3,8}\b"` outside the tokens/skins files must return 0).
JS-generated overlays (palette, dock) also inherit tokens through `.vt-*` classes.

| Skin | Identity | Default |
|------|----------|---------|
| `farshell` | Near-black (panes #121316, chrome #17181b) + **single blue accent** (#7aa2ff). Amber survives only as the `waiting` state (#f2b350). Not an impersonation of any OS — our own face. Since redesign phase 3 (2026-11) — it was near-black + amber accent (#f0a860) before | ✅ default |
| `macos` | iTerm2/Terminal.app — traffic lights, SF fonts, system blue, translucent title bar (`--color-titlebar-bg`) | |
| `catppuccin` | Pastel — no chrome, lavender accent | |
| `windows` | Windows Terminal — caption buttons, Cascadia, Fluent blue, Campbell terminal palette, square 2px controls (§2 Radius) | |
| `vscode` | VS Code integrated terminal — dark gray, Fluent blue accent, blue status bar (`--color-statusbar-bg`), square 2px controls (§2 Radius) | |
| `notepad` | Notepad/paper feel — the only **light theme**, warm off-white background | |
| `warp` | Warp terminal — near-black with a violet cast, single-token accent (`--color-acc`) drawn from a coral→lavender→sky gradient family that surface CSS (e.g. the settings page title) can paint as a gradient text-fill; rounder window corners than any other skin (the per-skin `--wrad` value of 16px was removed with the alias in 2026-11; the radius scale is shared) | |
| `linear` | Linear — near-black, one flat indigo accent, almost-invisible hairline borders (`--color-line` alpha .05, half of every other dark skin) | |
| `vercel` | Vercel/Geist — pure black/white + one blue signal color, hard edges (sharpest of all 9; its old 2px `--wrad`/`--trad` aliases were removed in 2026-11) | |

`warp`, `linear` and `vercel` are modelled on real products in FarShell's own
category (a terminal app and two dev-tool dashboards) and intentionally depart
from the restraint language in §0. Every value (backgrounds, the 4-step text
ramp, the accent, status colors) is solved algorithmically against
`scripts/check_contrast.py`'s thresholds rather than picked by eye: pick
values, compute contrast, adjust until passing.

**App icon.** `frontend/manifest.json` uses the `farshell` palette (`background_color`
`#121316` = `--color-bg-0`, `theme_color` `#17181b` = `--color-bg-1`, since redesign phase 9),
as does the `<meta name="theme-color">` in `index.html`. The icon art itself
(`frontend/icon-192.png`, `icon-512.png`, the favicon) is still the old purple tile — there
is no generator script in the repository, so redrawing it is deferred.

Switched from Settings → Appearance (모양), or the theme chip in the command
palette (`Mod+K`, shows up mixed into a bare search, or under the `>`
settings mode). A boot-time inline script in `<head>` commits `data-skin`
before first paint (anti-FOUC).

## 2. Tokens

| File | Holds |
|------|-------|
| `styles/theme/tokens.css` | Structural tokens (spacing, fonts, sizes, radius, shadow, easing, breakpoints, shell dimensions) + the default skin `farshell`'s color tokens. Declared as Tailwind v4 `@theme static`, which compiles to real `:root` custom properties |
| `styles/theme/skins.css` | The same color tokens remapped for the other 8 skins |
| `styles/layers/components.css` | `.status-dot` — the home for new components |
| `styles/screens/*.css` | Per-screen CSS in the `legacy` cascade layer: existing components, `.vt-*` overlays and the rail/dock/HUD frame. `legacy` is declared after `components`, so these rules beat component rules regardless of selector specificity |

Vite builds all four into `frontend/dist/app.css`.

### Color tokens (farshell baseline)

The contract is **21 colors** (5 background + 2 divider + 4 text + 5 accent incl. the
selection tint + 5 status, rewritten for redesign phase 3), plus the terminal colors
(`--color-term-*`), the chrome-bar colors, the scrim and 1 surface-state color that ride
alongside. Every token below is defined in **all 9 skins** (`tokens.css` for `farshell`,
`skins.css` for the rest).

The redesign mockups use short names (`--bg`, `--side`, `--sunk`, `--raised`, `--hover`,
`--acc`, `--line`, `--t1..t4`). **Those collide with the legacy aliases** in
`styles/screens/00-tokens-reset.css` (`--acc`, `--line`, …), so code only ever uses the
`--color-*` names: `--bg` → `bg-0`, `--side` → `bg-1`, `--sunk` → `bg-2`, `--hover` →
`bg-3`, `--raised` → `bg-raised`, `--t1..t4` → `txt/sub/muted/faint`, `--acc-soft` →
`acc-soft`, `--wait/work/done/err/idle` → `st-*`, `--term-*` → `--color-term-*`.

| Token | Value | Use |
|------|-----|------|
| `--color-bg-0` | `#121316` | Panes (the terminal area) |
| `--color-bg-1` | `#17181b` | Chrome — activity bar, sidebars, title bar, status bar |
| `--color-bg-2` | `#0f1012` | Sunk surfaces — inputs, row headers, subbars. **Darker than `bg-1`** (and, in `farshell`, darker than `bg-0` too): a sunk surface sits one notch below the chrome it is laid on (the order is not monotonic) |
| `--color-bg-3` | `#25272d` | Hover / pressed row and icon button |
| `--color-bg-raised` | `#202227` | Floating surfaces — menus, dialogs, toasts, sheets. The four text steps meet the same bars on it as on `bg-0..2`. `farshell`'s legacy `--menu` alias points here |
| `--color-line` / `--color-line-strong` | `rgb(255 255 255 / .07)` / `.12` | Two divider steps |
| `--color-txt` / `--color-sub` / `--color-muted` / `--color-faint` | `#ecebe8` `#a9a7a2` `#8c8a85` `#706e68` | Four text steps (`faint` is for disabled labels, placeholders and decorative numbers — never body text) |
| `--color-acc` | `var(--acc-farshell)` = `#7aa2ff` | The accent — focus, selection, the primary button. Kept, together with the 5 status colors, **for those roles only** |
| `--color-acc-dim` | `#5b80e0` | Badge border, outline |
| `--color-acc-surface` | `var(--color-acc)` (skin-overridable) | The surface under `acc-ink` text (primary buttons, active-tab underline background). Defaults to the accent itself — `macos`, `linear` and `vercel` override it, since white text on their accents measured under 4.5:1 |
| `--color-acc-ink` | `#0b1020` | Text on top of the accent |
| `--color-acc-soft` (+ `--acc-soft-pct: 13%`) | `color-mix(--color-acc 13%, transparent)` | The selected surface (selected row / tree item). Same rule as the waiting tint: on it, secondary text steps up one level |
| `--acc-<skin>` (9) | Each skin's own accent hex | **Out-of-scope constants.** The theme chip needs to show the accent dot of "a different skin than the one active now," so these live outside `data-skin`'s scoping alongside the scoped `--color-acc` — they're the single source both the chip dot and each skin's `--color-acc` read |
| `--color-st-{idle,working,waiting,done,error}` | `#7b7a76` `#52d1a0` `#f2b350` `#5fb8f0` `#f07070` | The 5 agent states (§5). `done` must be a different color from the accent (CIE76 ΔE ≥ 15) — a "finished" dot that matches the selection and the primary button reads as one of them |
| `--color-surface-active` | `rgb(255 255 255 / .18)` | The "currently selected" surface (rail, fleet home rows). Twice the hover overlay's alpha — the same value would be indistinguishable from hover on a pointer device. The accessibility requirement (WCAG 1.4.11) is carried by the row's left 3px status bar, not by this |
| `--color-st-waiting-tint` (+ `--waiting-tint-pct: 10%`) | `color-mix(--color-st-waiting 10%, transparent)` | Background of an attention-needed row (`.vt-srow.is-waiting`, Fleet). Used instead of `--color-surface-active`, which reads as a pressed grey "inactive" row on the light skin. On this tint the row's secondary text rises from muted to sub and the amber keyword to txt (the percentage is the ceiling — windows' yellow waiting colour leaves sub at 4.69:1) |
| `--color-on-solid` | `#ffffff` | Fixed white text over status banners, etc. |

**Tinted rows.** On both tints — waiting (`--color-st-waiting-tint`) and selection
(`--color-acc-soft`) — secondary text steps up one level: muted text is drawn in `sub`,
sub text in `txt`. Each token must therefore pass the bar of the role it is promoted into
on the tint (over `bg-0..2`): `txt` ≥ 6.0, `sub` ≥ 4.5, `muted` ≥ 3.0. The waiting row
already does this in CSS (`.vt-srow.is-waiting .vt-srow-meta`); no row uses `acc-soft`
yet — selected rows adopt it, with the same promotion, when they are redrawn (redesign
phase 4).

**Terminal colors** — `--color-term-*`. Everything that draws terminal content outside
xterm (pane preview cards, the palette's `@` preview, the work log's tool calls, the file
viewer, settings previews) uses only these. Each skin takes them from its own
`VT_XTERM_THEMES` entry in `frontend/js/theme.js`, so a preview matches that skin's
terminal (`frontend/tests/theme.test.js` checks it):

| Token | From `VT_XTERM_THEMES` | `farshell` |
|---|---|---|
| `term-bg` / `term-fg` / `term-bright` | `background` / `foreground` / `brightWhite` | `#121316` / `#d9d7d2` / `#ffffff` |
| `term-dim` | `brightBlack` (`white` on the light skin, where `brightBlack` is too pale) | `#77756f` |
| `term-red/green/amber/blue/mag/cyan` | `red/green/yellow/blue/magenta/cyan`; the `bright*` of the same name where the normal one is under 4.5:1 on the background (vscode and windows red/blue/magenta) | `#f07070` `#7fd18b` `#e8c46a` `#7aa2ff` `#c49cf0` `#6fc8d6` |
| `term-sel` | `selectionBackground` | `rgba(122,162,255,0.32)` |
| `term-add-bg` / `term-del-bg` | `term-green` / `term-red` at 12% | — |
| `term-hl` (search match) | `term-amber` at 35% (`catppuccin` 12%: its pastel yellow made the surface too light) | — |

Diff lines are drawn in `term-fg` on `term-add-bg`/`term-del-bg`; green and red mark the
`+`/`-` sign and changed words (3:1). Green or red as the text of a whole line cannot reach
4.5:1 on any visible tint in `notepad` and `vscode`.

**Chrome bars** (title bar 36, status bar 24 — redesign phase 2 draws them):
`--color-titlebar-bg`, `--color-statusbar-bg`, `--color-statusbar-fg`,
`--color-activity-bg`. They default to `bg-1` with `sub` text; `vscode` paints the status
bar its accent blue with white text (4.51:1), `macos` makes the title bar translucent
(`rgb(44 44 46 / .72)`; the blur goes on that bar only). `--color-scrim` is the layer
behind dialogs and sheets (`rgb(6 7 9 / .62)`; `notepad` uses a light ink scrim).

### Contrast rules

The bar differs per role, and the four text steps must stay visibly distinct
(adjacent steps differ by at least 1.25x in contrast against `bg-0`). The
thresholds live in `scripts/check_contrast.py` and are enforced across all 9
skins.

| Token | Bar | Basis |
|---|---|---|
| `txt` | **8.5:1** | Body text |
| `sub` | **6.0:1** | Secondary but still-read text |
| `muted` | **4.2:1** | Weak secondary text that must still be readable |
| `faint` | **3.0:1** | Disabled labels, placeholders, decoration — never body text |
| `acc`, `acc-dim`, `st-*` | **3:1** | Borders, dots, markers (WCAG 1.4.11) |
| `acc-ink` on `acc-surface` | **4.5:1** | Text on a filled accent button |
| `statusbar-fg` on `statusbar-bg` | **4.5:1** | Status bar text; `txt`/`sub` keep their bars on the title and activity bars |
| `term-*` on `term-bg` | **4.5:1** (`term-dim` **3.0:1**) | Terminal content; `term-fg` also on `term-sel`, `term-add-bg`, `term-del-bg`, `term-bright` on `term-hl` |
| Tinted rows | `txt` 6.0 · `sub` 4.5 · `muted` 3.0 | On the waiting and selection tints (see above) |
| `st-done` vs `acc` | **ΔE ≥ 15** (CIE76) | The two must not read as one color |

The text bars are checked on `bg-0`, `bg-1`, `bg-2` and `bg-raised` (`bg-3` is the
hover/active row, where text is only briefly seen). Translucent bars are composited over
`bg-0` first.

**Measured (worst case across 9 skins x `bg-0..2` + `bg-raised`, from
`python3 scripts/check_contrast.py --table`):**

| Skin | `txt` | `sub` | `muted` | `faint` | term ANSI (min) | `term-dim` | ΔE `done`/`acc` |
|---|---|---|---|---|---|---|---|
| farshell | 13.35 | 6.62 | 4.61 | 3.12 | 6.42 | 4.03 | 27.0 |
| macos | 11.20 | 6.09 | 4.28 | 3.05 | 5.21 | 3.14 | 52.5 |
| catppuccin | 8.69 | 6.29 | 4.29 | 3.06 | 7.08 | 3.36 | 25.5 |
| windows | 10.84 | 6.11 | 4.29 | 3.08 | 4.63 | 4.31 | 56.7 |
| vscode | 9.54 | 6.07 | 4.26 | 3.09 | 4.67 | 3.13 | 16.9 |
| notepad | 11.53 | 6.10 | 4.30 | 3.06 | 4.54 | 4.77 | 115.6 |
| warp | 15.48 | 6.70 | 4.21 | 3.15 | 7.14 | 4.76 | 50.3 |
| linear | 16.44 | 6.39 | 4.24 | 3.07 | 5.09 | 4.63 | 71.7 |
| vercel | 18.88 | 7.22 | 4.40 | 3.14 | 4.51 | 4.89 | 76.5 |

This table is recomputed on every CI run by
`frontend/tests/tokens-contrast.test.js` — you don't need to hand-update this
document every time a token value changes, but if the table drifts far
enough, refresh it.

### Interaction state rules (상호작용 상태 규칙)

One token set drives every pointer and keyboard state (`styles/theme/tokens.css`, per-skin values in `skins.css`).
Before (2026-11 audit §6): four focus-ring offsets (−2/+1/+2/0), two hover systems (`--menu-hover`, per-skin alpha .04~.10,
and `--color-bg-3`), 16 `:active` rules against ~137 hover rules, and no transition on the activity bar.

| Token | Value | Use |
|---|---|---|
| `--state-ink` | `#fff` · notepad `#000` · warp `#c4a6ff` | the overlay color of hover/press |
| `--color-state-hover` | ink 6% (`--state-hover-pct`) | hover on any surface |
| `--color-state-press` | ink 11% | `:active` — one step above hover |
| `--color-state-selected` | accent 13% (= `--color-acc-soft`) | selected row/segment/toggle |
| `--color-state-selected-hover` / `-press` | accent 18% / 23% | hover/press **on a selected row** |
| `--color-state-waiting-hover` / `-press` | `--color-st-waiting` 14% / 18% (catppuccin 13/17, notepad 17/21) | hover/press on a "needs you" row (rest tint 10%) |
| `--focus-ring-w` / `--focus-ring-offset` / `--focus-ring-inset` | 2px / 2px / −2px | the ring |
| `--color-focus-ring` | `--color-acc` (macos `#3b9dff`, vscode `#3395d6` — lightened so 3:1 holds on hover/tint) | the ring color |
| `--dur-fast` / `--ease-fast` | 120ms / ease-out (0ms under `prefers-reduced-motion`) | the only transition for states |

Rules:

- **Steps are visible and ordered.** rest < hover < press, and selected < selected-hover < selected-press. Hover must not vanish
  on a selected or waiting row — `styles/screens/99-states.css` is the one file that maps those rows (it loads last so it beats the
  row tints). `check_contrast.py` and `tokens-contrast.test.js` check, on all 9 skins and bg-0/1/2/raised: text contrast on every
  state surface (hover/selected-hover/waiting-hover txt 6.0 · sub 4.0 · muted 2.9; press 5.0 · 3.5 · 2.5), hover vs rest (≥ 1.08),
  ΔE between hover/press (≥ 4), hover/selected (≥ 5), selected/selected-hover (≥ 3), and the ring on every state surface (≥ 3:1).
- **One focus ring**: `:focus-visible { outline: var(--focus-ring-w) solid var(--color-focus-ring); outline-offset: var(--focus-ring-offset) }`
  in `00-tokens-reset.css`. Consumers never write `outline`. Where a container's `overflow` would clip a +2 ring (bars, list rows,
  text inputs, selects), the element goes on the **inset list** right under that rule, which swaps the offset to `--focus-ring-inset`.
  `state-tokens.test.js` fails on any other `outline` and on `--menu-hover`/`--color-bg-3` hover backgrounds.
- **Every hover has a press**: `:active` uses `--color-state-press` (rows, buttons, menu items, tabs, activity-bar icons); solid
  buttons and the selected segment use `filter: brightness(1.08 / .94)` because their face is the accent.
- **Cursor and disabled** come from `styles/layers/states.css` (zero-specificity base in `@layer components`): `pointer` on everything
  clickable, `not-allowed` + opacity .45 on disabled/`aria-disabled`.
- **Transitions** are `background-color`, `color`, `border-color`, `opacity` at `--dur-fast`/`--ease-fast` and nothing else — never
  width, padding, margin or position (a layout transition re-fits the terminal). Data bars (gauges) may animate `width`.
  `prefers-reduced-motion` sets `--dur-fast` to 0.
- **Tooltip** (`.vt-tip`): `bg-raised`, 1px `--color-line-strong`, `--shadow-e2`, 12px; title and `data-tip-sub` stack at the same x;
  max width `min(280px, 100vw − 16px)`. It stays inside the trigger's panel (sidebar/dock): the side flips, then goes above/below
  with x clamped to the panel (`ui/tooltip-place.js`, unit-tested); the activity bar is exempt (its names open over the sidebar by
  design), and `data-tip-free` opts out. 400ms to open, instant within 300ms of the last close, Esc dismisses.
- **Menu** (one spec for every menu — FarShell menu, row/`+`/host menus, file-tree row menu, dropdown lists, port sheet; see
  `components.css` "메뉴 한 규격"): surface `bg-raised` + 1px line-strong + radius 8 + e2 + padding 4; item 28 (phone/coarse 44),
  13px, padding 0 8, gap 8, radius 4; icon 14 in `muted` (`on` accent, `danger` error); right column = shortcut (mono 12 muted,
  `.vt-mb-keys`/`.vt-ft-mi-k`) or detail (ui 12 muted); separator 1px × −4px margins; section label 11/600 muted. Danger items get a
  separator above them automatically. A menu opened from a header button opens beside the sidebar, not below the header (it used to
  cover the sort control).

### Structural tokens

- **Spacing:** a single `--spacing: 4px` scale. Every margin is
  `calc(var(--spacing) * N)`.
- **Font size:** the redesign's seven steps **11 / 12 / 13 / 15 / 18 / 22 / 28**. The existing
  `--fs-*` names were kept and moved to the nearest step (renaming 440-odd consumers is not a token
  change): `3xs` 11 (was 10.5) · `2xs` 11 · `xs` 12 (12.5) · `sm` 13 (13.5) · `base` 15 · `md` 18 (17) ·
  `lg` 22 (20) · `xl` 22 (24) · `2xl` 28 (30). `3xs`/`2xs` and `lg`/`xl` now share a value. `3xl`/`4xl`
  (38/46) stay outside the scale — they size three empty-state glyphs, not text.
- **Radius:** `--radius-xs`(**4px** — small 24px buttons, chips, menu items) / `--radius-sm`(**6px**,
  was 5 — every control: 28px buttons, inputs, selects) / `--radius-lg`(**8px**, was 10 — menus,
  dialogs, cards, sheets) / `--radius-full`. There is no `--radius-md`. The old per-skin `--wrad`/`--trad` aliases are gone (2026-11); only
  `windows` and `vscode` override the scale: `--radius-xs`/`--radius-sm` are **2px** there, the square controls of
  Windows Terminal and VS Code, while `--radius-lg` frames (menus, dialogs, cards, sheets) stay as in every other skin.
- **Shadow:** `--shadow-e1` (the current row) + `--shadow-e2`/`e3` (overlays).
  e1 must be defined in every skin — see §0 for why.
- **Motion:** `--dur-fast`(120ms) / `--dur-base`(200ms), `--ease-out-quint`; interaction states use `--dur-fast` + `--ease-fast` only (§ "Interaction state rules").
- **Breakpoints:** `--breakpoint-regular`(720px) / `--breakpoint-wide`(1280px) /
  `--breakpoint-xwide`(1600px). The JS-side constants live in exactly **one**
  place, `frontend/js/layout/breakpoints.js` (`COMPACT_MAX`/`REGULAR_MAX`/`WIDE_MAX`).
- **Shell dimensions:** title bar 36px (top, full width; the only header at >=720px) · status bar 24px
  (bottom, full width) · activity bar 48px ·
  one sidebar, default 360px (resizable 240~480, at most 40vw; regular draws at most 260), **shared by every
  activity**. Collapsed, the sessions activity leaves the rail's 48px strip and a tool activity leaves 0. The
  sidebar's width/activity/collapse state are **device-scoped settings** (`ui.side.*`, §6 "Settings") —
  collapsing it on a phone must not affect the Mac.

The values below have **the code as the single source of truth**; this
document merely copies them. A copied value eventually drifts, so
`check_design_md()` in `scripts/check_docs.py` diffs this table against the
code directly in CI (I3). Why a table rather than scanning prose: a bare
number like `7` or `720` always exists somewhere in a doc's prose, so
scanning prose lets a wrong value slip through unnoticed.

### Code-derived values

| Key | Value | Source |
|---|---|---|
| `skins` | `farshell, macos, catppuccin, windows, vscode, notepad, warp, linear, vercel` | `frontend/js/theme.js` — `VT_SKINS` |
| `breakpoints` | `720/1280` | `frontend/js/layout/breakpoints.js` — `COMPACT_MAX`/`REGULAR_MAX` |
| `pane-cap` | `1/4/6` | `frontend/js/layout/dnd.js` — `tierCap()` (compact/regular/wide; xwide is `Infinity`, so it isn't captured in the table) |
| `activity-items` | `sessions, scm, dir, queue, files, snippets, ports, usage, system` | `frontend/js/shell/activity-data.ts` — ids of `ACTIVITIES` in order (the activity bar's upper group, §3; `ports`/`usage` are capability-gated at runtime but always listed). Replaced `rail-items` (the legacy icon rail, removed in redesign phase 9) |
| `agent-states` | `5` | `server/agent_status.py` — `STATUSES` (`error` is reserved, §5) |
| `color-bg-0` | `#121316` | `styles/theme/tokens.css` |
| `color-acc` | `#7aa2ff` | `styles/theme/tokens.css` |
| `color-txt` | `#ecebe8` | `styles/theme/tokens.css` |

### Legacy aliases

`--win`/`--bar`/`--acc`/`--txt`/`--sub`/`--line`/`--ok`/`--err` etc. in `styles/screens/00-tokens-reset.css` are aliases pointing at the `--color-*` tokens
above (`--menu-hover`, `--crust`, `--wrad` and `--trad` were migrated to `--color-state-hover`, `--color-scrim`/`--color-bg-2` and
`--radius-*` in 2026-11 and deleted; none of them was an externally documented variable). Bulk-replacing the hundreds of consumers with `var(--color-*)` is a
separate, much larger job of moving where the value comes from — it hasn't
been done. **New code reaches for `--color-*` directly.**

### Typography

`--font-ui`: Wanted Sans Variable (bundled; covers Korean and Latin, UI chrome). `--font-mono`:
JetBrains Mono (bundled Latin subset), then Wanted Sans so Korean text inside monospace
runs does not fall back to the OS font — neither path makes a
network request. The terminal stack ends with the bundled glyph-only fallback
`Symbols Nerd Font Mono` (MIT, ~1.1MB, fetched lazily when a PUA glyph first
appears) so prompt icons / Powerline separators render on any device.

OS-mimicking skins use each OS's system font. There, `system-ui` isn't "gave
up on typography" — it's the **authentic choice for impersonating iTerm2 /
Windows Terminal**.

- macOS: `-apple-system, "SF Pro Text"` (UI), `ui-monospace, "SF Mono", Menlo` (terminal)
- windows: `"Segoe UI"` (UI), `"Cascadia Code", "Cascadia Mono", Consolas` (terminal)
- catppuccin: `system-ui` (UI), `ui-monospace, "SF Mono", Menlo, Consolas` (terminal)

## 3. Shell layout

There is no `⋯` overflow menu.

**A terminal lives inside the pane that shows it**
(`.vt-pane > .vt-pane-body > .vt-term-wrap`). Do not reintroduce a separate
absolutely-positioned terminal layer joined to the pane chrome by coordinates:
the terminal would not be a descendant of its pane, so `overflow: hidden` and
`border-radius` on the pane could not clip it, an accent *surface* could not be
used for the active pane (the terminal would paint over it, leaving a single 1px
outline as the only signal), and xterm's cell quantization remainder (measured
22×8px) would be stranded with no way for the pane to treat it as padding.
`panes.js` reuses split/pane elements by id, and the refit gate fires only when
a rect's width or height actually changed. Moving an xterm wrapper in the DOM is
safe: re-parenting a live **WebGL2** terminal leaves `isContextLost()` false,
raises no `webglcontextlost`, keeps the buffer (1128×828 measured), and keeps
drawing. `layout/surface.js` holds the refit gate — which needs no
coordinates — plus `#vt-term-stage`, a hidden **waiting room** holding the
wrappers of sessions that are not currently placed in any pane (they must
stay in the DOM, and `visibility: hidden` keeps their box measurable so
cols/rows survive).

```
┌ title bar 36 (full width, fixed) ────────────────────────────────────────┐
│ FarShell ▾ · host ▾ · repo › branch › session  [ search… ⌘K ]   [kb] [⎯] │
├──────┬──────────────┬────────────────────────────────────────────────────┤
│ act. │ sidebar 360  │ pane tree                                          │
│ bar  │ (one width,  │ ┌ pane header 24 ┐                                 │
│ 48   │  shared by   │ │ cwd · agent     │                                │
│      │  activities) │ └─────────────────┘                                │
│ sess │ sessions =   │                                                    │
│ scm  │   rail       │                                                    │
│ …    │ tools =      │                                                    │
│ mcp  │   dock body  │                                                    │
│ mic  │              │                                     ┌ pinned 300 ┐ │
│ set  │              │                                     │ (optional) │ │
├──────┴──────────────┴─────────────────────────────────────┴────────────┴─┤
│ status bar 24: [agents] [message] [E2E safe screens] … [CPU RAM NET usage] [v] [clock] │
└──────────────────────────────────────────────────────────────────────────┘
```

### 4 responsive tiers

| Tier | Width | Workbench (activity bar + sidebar) | Pane cap | Rendering |
|---|---|---|---|---|
| compact | < 720px | none (fleet home and bottom nav, §7) | 2 | 1 pane fullscreen, swipe left/right |
| regular | 720~1279px | collapsed by default; expanding pushes the panes at `min(stored, 260px, 40vw)`; a pinned tool floats over the panes as a drawer | 4 | split tree |
| wide | 1280~1599px | open by default at `min(stored, 40vw)`, pushes the panes; a pinned tool takes 300 on the right | 6 | split tree |
| xwide | ≥ 1600px | same | **unlimited** | same as wide + cap lifted |

> The legacy 48px icon rail (`#vt-rail`) and the 2.0 right usage rail (`#vt-right-rail`) were
> removed from the DOM in redesign phase 9; the workbench is the only left edge at every tier.

The tier touches the sidebar in exactly two places, and **neither overwrites what
you chose** — `ui.side.width`/`ui.side.collapsed` are device-scoped settings
(§6), and a stored value always wins (`shell/side-prefs.js`).

- **Collapse default**: on a device that has never stated a preference, the sidebar
  starts collapsed below wide (<1280) — a narrow screen does not open with the sidebar
  expanded and leave the panes too little room.
- **Rendered width clamp**: `--vt-side-w` is `min(<stored>px, 40vw)`, and in regular
  `min(<stored>px, 260px, 40vw)` (`styles/screens/90-workbench.css`, media queries — no JS on
  window resize). The stored value says how wide you want it; the clamp asks whether the
  window can spare that. Shrink the window and the sidebar draws narrower; widen it and it
  returns — the setting is never rewritten.

Because the variable holds a `min()` expression rather than a plain px
number, the resizer reads the element's measured width instead of parsing it —
a custom property doesn't resolve to computed px, so `parseInt` would return
`NaN` and the sidebar would jump on drag.

**Old keys are read, never rewritten.** With no `ui.side.width` the width falls back to
`ui.dock.width`, then `ui.rail.width` (clamped into 240~480); with no `ui.side.collapsed` the
collapse state falls back to `ui.rail.collapsed`. Nothing copies the old values into the new
keys. The tier boundaries live in exactly one place, `layout/breakpoints.js`. Past the cap, the
split button goes disabled **with the reason in its tooltip**.

### View tabs

**There is no tab strip.** The rail switches between sessions and grouped views (clicking a row goes to
that view and focuses its pane), names a group (the group header's Edit (편집)) and closes, so a strip would be a second copy
of the same navigation. There is no `#vt-wtabs`, per-tab state bar or tooltip, right-click tab menu or drag-to-reorder. The **view model** remains:
a view is still one tab in `layout/store.js` that owns one pane tree, because grouped layouts, a viewer/panel pane that
belongs to a session and the remembered closed groups are all stored per view (docs/ref/terminal.md「Session and view
rules」). There are no next/previous-view or move-view keys either; view order changes only in the phone's order sheet. Think of a view as just a group (or a session with its viewer panes), not as something you navigate.

At >=720px `#topbar` is `display: none` (`--topbar-h: 0`); the traffic lights (macOS skin), the keyboard toggle
(touch devices) and the Windows caption buttons move into the title bar (`#vt-mb-lead` / `#vt-mb-tools`,
`layout/menubar-slots.js` — they are **moved**, not copied, so ids and listeners survive) and move back below 720px.
`#palette-toggle` (`⌘K`) stays in `#topbar`: the title bar's palette field does the same job.
Voice-only mode keeps `#topbar` because it turns it into the full-screen container. Dragging a rail row onto a pane
still uses `layout/dnd.js`.

### Activity bar 48px + sidebar (`#vt-workbench`, `shell/Workbench.tsx`)

The workbench is the left edge of the desktop shell: an **activity bar** (48px, `shell/ActivityBar.tsx`)
and **one sidebar** beside it. The bar's top group are the activities, in this order: Sessions (세션) (`list`) ·
Changes (변경) (`git-branch`) · Files (파일) (`folder`) · Queue (큐) (`clipboard`) · Attachments (첨부) (`paperclip`) · Snippets (스니펫) (`braces`) · Ports (포트)
(`plug`, capability-gated) · Usage (사용량) (`gauge`, capability-gated) · System (시스템) (`bar-chart`)
(`shell/activity-data.ts`). The bottom group are actions that do not change the sidebar: Manage MCP · skills (MCP · 스킬 관리)
(`box`, the lazy `ui/icons-shell.js`), Voice (음성) (the `#mic-btn-wrap` node itself, moved into
`#vt-activity-mic-home` by `term/keybar.js` `placeMicButton` from its hidden parking slot `#vt-mic-park`)
and Settings (설정) (opens Settings; the approval-pending badge from `share-lazy.js` sits on its icon corner).

**Order and hiding are the user's choice** (Settings → Status bar · activity bar (상태 막대 · 활동 막대), right column Activity bar order (활동 막대 순서), mock
O05; device-scoped `ui.activity.layout`, JSON `{"v":1,"order":[…],"hidden":[…],"bottom":[…]}`, `''` = default).
Sessions (세션) is pinned first and cannot be hidden. A hidden activity leaves only the bar: the palette and keymap still open
it and the sidebar shows it as usual (`available` keeps it; only the bar's `items` drop it). The bottom group can
be reordered but not hidden (hiding the gear would remove the way back) — by CSS `order`, because the mic slot is
the node `voice.js` caches. An activity missing from a saved layout (a newer version's) is appended in default
order; unknown ids are dropped. Rules: `shell/activity-layout.js` (plain JS, like `hud-layout.js`). The editor
(`panels/settings/sections/activity-order.js`) is grip + icon 14 + name, a Pinned to top (맨 위 고정) or Hidden (숨김) tag, an eye
toggle (`aria-pressed`) for the top group and ↑↓ 24; drag and the arrows share `moveActivity`. It sits in the
right column at ≥1100px and below the status bar zones when narrower (the same editor drawn twice, CSS picks one).

- **Item** 48×44, icon 20 (stroke 1.75), name only in `aria-label` and the tooltip (`data-tip`, shown to
  the right; second line = badge count, plus the `railToggle` key on the current activity). **Active** =
  a 2px `--color-acc` bar on the left edge (10px inset top and bottom, `::before`), no fill. A badge
  (waiting sessions on Sessions (세션), queue waiting, open ports) is a `.vt-badge` 15px high on the icon's top-right
  corner. The Sessions badge counts every waiting session — even with the Needs attention (개입 필요) zone hidden — and is amber
  (`data-state="waiting"`); the others are neutral. The rail reports the count (`onWaitingCount`); it is
  not polled separately.
- **Accessibility:** the top group is `role="tablist"` with `aria-orientation="vertical"`; each activity is
  `role="tab"` with `aria-selected` and roving `tabindex`. ↑↓/Home/End move focus only; Enter/Space
  (the button's own click) opens it. The bottom group is a plain `role="group"`.
- **The sidebar width is shared by every activity** (default 360 — 320/392 with the small/large Menu size (메뉴 크기) —, 240~480, ≤ 40vw; `ui.side.width`).
  Switching activities therefore never changes the pane sizes — no terminal refit, no SIGWINCH. The
  sessions activity shows the worktree rail below; tool activities show the dock body (below). Both stay
  **mounted** and only toggle `hidden`, so the rail keeps its polling and in-place editing state.
- **Collapse:** clicking the current activity while the sidebar is open collapses it, as does `railToggle`
  (⌘B / Alt+Shift+B, Toggle sidebar (사이드바 토글); `window.vtSideToggle`). Sessions collapse to the rail's 48px strip;
  a tool collapses to 0 (only the activity bar remains). Clicking any activity while collapsed opens it.
  Stored as `ui.side.activity` (default `sessions`) and `ui.side.collapsed`.
- **Resizing:** one resizer on the sidebar's right edge. While dragging, the refit gate in
  `layout/surface.js` defers terminal fits (`window.vtSurfaceDrag('begin'|'end')`, a bridge because the
  lazy chunk must not import `surface.js`); the fit — and the WS `resize` — happens once per pane after
  release, and the width is saved once. No CSS transition on any width or padding.
- **Drag an activity into a pane** like the old dock tabs (`vtWirePanelDragSource`); if that tool was the
  one open in the sidebar, the sidebar collapses (`vt-panel-pane-drop`).
- Positions that depend on the left edge read `--vt-activity-w` + `--vt-side-w` (body padding, onboarding,
  the viewer's dock mode). The right edge reads `--vt-pin-w` — 300 when a tool is pinned at wide, else 0.

### Right pinned sidebar (`#vt-side-right`, `ui.side.pinned`)

A tool panel can be pinned as a **second sidebar on the right** (mock D01: sessions on the left, Changes (변경) on the
right). The panel head's Pin to the right (side by side) (오른쪽에 고정(나란히 보기)) button (`panel-right`, inserted before the close X by
`Dock.tsx`) pins it; the same button — now `panel-left`, `aria-pressed="true"`, accent — unpins it back into the
left sidebar. The setting is `ui.side.pinned` (device scope, the **panel id** such as `vt-dock-scm`, `''` = none).

- **Width 300, fixed** (no resizer). The left sidebar keeps its shared-width rule; pinning moves the left sidebar
  back to Sessions (세션) without changing its width.
- **Wide (≥1280):** takes 300 beside the panes (`--vt-pin-w`, body `padding-right`). **Regular (720~1279):** a
  floating drawer over the panes with `--shadow-e2`, so pane sizes do not change. **Compact:** none (panels are
  sheets). Focus mode hides it (`--vt-pin-w: 0`).
- **One panel, one place.** A panel id is global, so the pinned tool cannot also be the left activity: its activity
  button shows a 4px accent dot in the top-right corner (`.vt-act.pinned::after`) and clicking it focuses the
  pinned panel instead. Pin/unpin **moves the panel node** (polling, scroll and edit state survive), which is why
  `#vt-side-right` is always mounted and only toggles `hidden`.
- Closing the pinned panel (its X, or toggling the same command from the palette) unpins it.
- Pin and unpin change the pane width once; both are wrapped in the refit gate (`vtSurfaceDrag('begin'|'end')`)
  so each pane fits — and sends a WS `resize` — once.

### Session view — worktree rail (`#vt-wgrail`, `shell/Rail.tsx`)

A row is **one tmux session** (docs/ref/terminal.md 「Rail」). Two zones: **Needs attention (개입 필요)**
(waiting sessions; collapsible, hideable with the eye button in its head or in Rail settings (세션 목록 설정), like VS
Code's Open Editors) and **Running (실행 중)** (every other running session, **name order** or manual drag
order — no urgency sorting, so rows never jump). Below them, **Recently ended (최근 종료)** (collapsible, newest N)
and a one-line **Library (보관함)** that swaps the list in place. There are no "grouped" (묶음) / "ungrouped" (묶지 않음) / "sleeping" (잠자는 중) zones.

**Header 36** (`.vt-wgrail-head`): 「Sessions N」 (세션 N) 12/600 (or the host switcher when there is more than one
host) and four 24px icon buttons — Conversation history (대화 기록) (`history`), Rail settings (세션 목록 설정) (`sliders-horizontal`), New session (새 세션)
(`plus`), Collapse (접기) (chevron). **Section heads 28** (`.vt-wgrail-group-head`): chevron 12 · name 12/600 ·
count mono 11, actions on the right; Needs attention (개입 필요) is amber. Grouped views use the same head with a
`layout-panels` icon and a pencil (rename). **Footer 36**: a quiet 「+ New session」 (+ 새 세션) button.

**Row anatomy** (redesign phase 4; specimen D02): padding 6/6/6/12, a 16px icon column + 8 gap, lines
20/18/18/18. Rows are separated by a 1px `--color-line` (not after a section head).

1. **State icon** (`stateIcon` 16, in its own column; a ring around it = unseen) · agent mark 14 ·
   **name** 13/600 (edited in place) · the delegation toggle Delegation reports 1/2 (위임 보고 1/2) · **elapsed** (mono 12,
   right) · ⋯ (shown on hover, focus or when active; always on touch).
2. Mono 12: `WT` (separate worktree) · directory · `git-branch` + branch — **main is shown too** — ·
   change size (`+add` in `st-working`, `−del` in `st-error`, or `파일 N`). With no context yet:
   Waiting for update (정보 갱신 대기) (`faint`).
3. **State keyword** 12/600 · Tools (도구): name (the one place allowed to ellipsize) · badge chip · internal
   subagent chip (2 internal working (내부 2 작업 중), `st-working` tint) · share mark · **origin icon + short text** (local has none;
   `laptop` Created on another Mac (다른 맥에서 만듦), `square-terminal` Created directly (직접 만듦); the text ellipsizes first, the tooltip keeps the long
   sentence). The server does not record which Mac created a peer session, so the mock's Created on m5-studio (m5-studio에서 만듦)
   reads Created on another Mac (다른 맥에서 만듦). The phone's Fleet keeps the icon only.
4. Only when applicable: Other screens N (다른 화면 N) (`monitor-smartphone`) and the non-web attach count (`terminal`).

The state keyword is short and fixed so it is never truncated: Approval request (승인 요청) / Choice requested (선택 요청) / Plan review (계획 확인) / Question (질문)
for waiting (server `kind`), Idle (유휴) / Working (작업 중) / Done (완료) (+ Has question (질문 있음) / Has plan (계획 있음) from the server `badge`,
which working rows show too — agy's plan badge rides a working entry). There is no left status bar, repo
color bar, tmux plug icon or `? question` line — state is the icon's colour **and** shape.

**Row surfaces.** Selected (the pane you are looking at) = `--color-acc-soft` + a 2px `--color-acc` bar
on the left; waiting = `--color-st-waiting-tint` + a 2px `--color-st-waiting` bar (both are inset
box-shadows, so `::before`/`::after` stay free for branch lines). **On both tints the secondary text
steps up one level** (muted → sub, sub → txt; §2 「Tinted rows」). Idle rows step the name down to `sub`
500; a session that is not open in this tab steps it down to `muted` 500 — never opacity.

**Inline answers.** A waiting row that is open in this tab gets an answer line below its text: one 24px
button per detected option (「1 · 예」 「3 · 아니오」, long labels ellipsize with the full text in the
tooltip) and a quiet Go to terminal (터미널로) that opens the session. It is the same `AnswerButtons` component and the
same `answerSessionPrompt` call (`shell/rail-fetch.ts`, with `prompt_id`) as the phone's Fleet; clicks
never bubble to the row, and the buttons lock until the reply returns.

**Delegation and subagents.** Delegated child sessions sit under their parent, indented 24 (icon at
x=36), joined by a 1px `--color-line-strong` branch line on x=19 (the parent icon's axis); the short turn into
each child takes the child's branch colour (merged `--color-st-done`, failed `--color-st-error`, closed
`--color-line-strong`, otherwise the parent's lane), the same classes as the collapsed rail and the phone Fleet; their line 3
is the delegation state plus Separate terminal (별도 터미널). Native subagent rows (`NativeChildren.tsx`) use the same
indent and line: state icon 16 · name 13/500 · elapsed, then Internal · Working (내부 · 작업 중) in the state colour.
Ended rows (`StoppedRow`) use the same columns with the dashed state circle and a `sub` name; their
action line (Check again (다시 확인) · Unarchive (보관 해제) · Resume (이어서 시작) — only Resume is accent-filled) appears on hover or
focus with a fine pointer and is always shown on touch.

**Collapsing.** The Running (실행 중) head is a toggle like Needs attention (개입 필요) (caret · label · count; the sort switch stays), and
each grouped view has its own caret before the group icon (the title still opens the group; collapsed groups keep
their head and count). Both are device-scoped in `rail.view` (`runningCollapsed`, `groupsCollapsed` = view ids,
newest 200).

The ⋯ button and right-click open the same menu (`rowMenuItems`: Open (열기)/Close (닫기) · Rename (이름 변경) ·
Open on Mac (맥에서 열기) · Connected screens (연결된 화면) · Kill (종료)). **Rail settings (세션 목록 설정)** (sliders) holds Needs attention (개입 필요) on/off, show or hide sessions
by origin — peer / external, and the repository display list (저장소 표시). Hiding an origin never hides a session that is
open or waiting. The rail fills the sidebar; its width and collapse state belong to the workbench
(above), and the header's chevron toggles the same collapse. **Collapsed (48px strip, ring design)**: 40×40 cells
(radius 6) with the agent mark 18 centred in the **state colour** inside a 2px state ring (idle dotted · waiting dashed and
breathing · working rotating arc · done solid — the shape says the state, not only the colour; motion stops under
`prefers-reduced-motion`). 「Not seen yet」 is a 6px dot at the mark's lower right (2px cut-out in the cell colour); a 12px badge at the
upper right counts working internal sub-agents (name only; the details are in the hover card). Delegation children sit under their parent as 0.8× cells with a 2px branch on
the left. The order is the **expanded rail's tree order** (Needs attention first, then the configured sort / manual order / split groups, children
under parents), and the first icon's centre lines up with the expanded first row's (body top padding 24, 32 between sections). Colours are
the `--color-st-*` tokens, 3:1 or better on every cell background in all 9 skins (`tokens-contrast.test.js`); the badge text uses
`--color-on-st-working`. **Menu size** (Settings › Appearance (모양), `ui.side.scale`, desktop only): small / normal / large = activity bar and strip
44 / 48 / 52, cell 36 / 40 / 44, session rows one text step smaller / as is / larger, default sidebar width 320 / 360 / 392 (a stored
`ui.side.width` always wins); one `data-vt-side-scale` attribute on `<html>` drives `--vt-act-w` and `--vt-cell`.
Hovering or keyboard-focusing a collapsed cell opens a **detail card** (300px, `bg-raised` + 1px `line-strong` + radius 8 + `--shadow-e2`, 12/13 text, mono + tabular
numbers for paths and counts, row height 20 on a `[14px icon | value | right value]` grid, 8 between sections, opacity-only 120ms entrance and none under
`prefers-reduced-motion`; text uses only `txt`/`sub`, state colours only on shape glyphs). Content and rules: [terminal.md](docs/ref/terminal.md) (「Hover card on the collapsed strip」).

### Tool views — dock body (`#vt-dock`, `shell/Dock.tsx`)

There is no separate right-hand dock and no dock tab strip any more: the tabs became the activity bar's
tool activities, and the dock is the sidebar's **tool view**. `Dock.tsx` keeps three jobs — "only the open
tool activity's panel and the pinned tool's panel are in the DOM" (it calls the panel's action and `closePanel`s
the others), the `window.vtDockHost(panelId)` contract `panels/panel.js` asks (an element to attach to — the left
body or the pinned body —, `null` for a modal, or `'stale'` when the activity changed while the chunk was
loading), and the pin button in each docked panel's head. Each tool's content is drawn by the
existing panel renderers (`queue.js`, `ports.js`, `panels/usage.js`, `panels/viewer/scm.js` …) into
`.vt-dock-body`. There are no modal popup panels; the action ids (`queue.show`, `ports.show`,
`usage.open`, `scm.show`) open that tool activity in the sidebar (the palette and keymap reference those
same ids). The ports and usage activities disappear entirely when `/api/capabilities` reports no source
for them. In compact there is no workbench, so no tool is treated as open and the panels fall back to
their sheets.

**Tool panels share one set of metrics** (redesign phase 5, the panel kit at the top of
`styles/screens/80-dock.css`): head 40 (title 15/600 + an 11px mono context such as `저장소 / 브랜치`,
`N건 대기` or `사용 / 한도`, then icon actions), section head 28 (`.vt-psec`: name 12/600 `sub`, count 11 mono
`muted`, actions or a note on the right), rows 36 and two-line rows 48–52 (`.vt-prow`, inset 4px with a 6px
radius; hover `bg-3`, selected `acc-soft`), inner padding 12/14 (`.vt-ppad`), inputs 28, buttons 24/28, sub-screen
head 36 (`.vt-psub`: ← plus what you are looking at — a diff, a commit, the snippet editor), cards with an 8px
radius and 12px padding (`.vt-pcard`, the one in use gets an accent border), 4px bars (`.vt-pbar`, `st-waiting`
from 75%) and 16px outlined word tags (`.vt-ptag`, tone `acc`/`work`/`wait`/`err`). The body is `bg-1`, the same
surface as the sidebar. Row actions appear on hover or focus (always shown under a coarse pointer); a file's git
state is a letter (M/A/D/U/R/!) **and** a word, never colour alone. Panel-only icons live in
`panels/panel-icons.js` (the `panel` chunk every panel already loads), not in core `ui/icons.js`. Panels in a
pane use the same classes. In the 720px+ sidebar `.vt-input.lg` drops to 13px (the 18px size exists for iOS zoom
on phones, which get sheets, not `.vt-dock-panel`).

**The source-control tab supports status, diff, stage, unstage and commit.**
Push and PR/MR creation are not offered. Commits use the repository's ambient Git
configuration; the stored git accounts are not connected to this workflow.
See [docs/ref/worktree.md](./docs/ref/worktree.md) for the guards.

**The code viewer is separate from the dock file tree.** Files open in the configured
`sheet`, `float` or `pane` surface; compact screens use a fullscreen sheet.
See [docs/ref/files.md](./docs/ref/files.md). In `layout/tree.js`, a pane leaf carries `kind: 'terminal' | 'viewer' | 'panel'`. A `viewer` leaf has
only a file path; a `panel` leaf has one registered dock panel name. Both keep
`session: null`.

### Title bar 36px (`shell/TitleBar.tsx`, element `#vt-titlebar`)

The top edge of the desktop shell (>=720px), `position: fixed` across the full width. Its height is the CSS
variable `--titlebar-h` (36px; 0 in compact) — set in CSS, not measured, so the lazy chunk arriving late never
changes the pane sizes (no refit, no SIGWINCH). `body { padding-top }` and the sidebar's `top` use it.
`--menubar-h` is kept as an alias of `--titlebar-h` for everything that drops below the top edge
(`--topbar-popup-y`, onboarding, the dock viewer, the palette and dialogs).

Three columns (`grid-template-columns: minmax(0,1fr) auto minmax(0,1fr)`, equal side padding) so the palette field
sits exactly at the window centre and the sides shrink first:

- **Left:** `#vt-mb-lead` (macOS traffic lights) · the **FarShell menu** (`MenuButton`, class `.vt-mb-menu`: New session (새 세션) ·
  Quick open (빠른 열기) · Focus mode (집중 모드) · Copy access URL (접속 주소 복사) · Settings (설정) · Status bar settings (상태 막대 설정); the dropdown is a `Portal` into `body`; Esc/outside click/↑↓/Home/End
  work; the approval-pending badge from `share-lazy.js` attaches to `.vt-mb-menu`) · the **host** (`#vt-workspace-chip`:
  the Mac the active pane shows — `laptop` icon locally, `server` + accent when it is another Mac; with two or more
  registered hosts it is a button that opens the host list and switches the whole screen, like the rail header's
  switcher; with one host it is a plain label) · the **breadcrumb** Repository (저장소) › Branch (브랜치) › Sessions (세션) of the active pane's session
  (`layout/branch-chips.js` — the same source as the pane-header branch chip; the repository is the *main*
  worktree's folder name; unknown parts are omitted; hidden in regular).
- **Centre:** the **palette field** (`#vt-tb-palette`, 360×24; 280 in regular; below 900px only the icon and `⌘K`).
  It is a button, not an input: clicking opens the existing palette (`quickopen.open`).
- **Right:** `#vt-tb-focus-slot` holding the **focus-mode button** (`#vt-tb-focus`, 28px, `maximize`; in focus mode,
  when the title bar is revealed from the top edge, it shows `minimize` pressed and exits — see 「Focus mode」 below) —
  and `#vt-mb-tools` (keyboard toggle on touch devices, Windows caption buttons). `#vt-mb-lead` and `#vt-mb-tools`
  are static nodes in `index.html`; after mount `TitleBar.tsx` moves them into its own childless elements so Solid
  never re-renders over them.

**Copy access URL (접속 주소 복사)** copies the public tunnel URL if one is running, else `location.origin`; it never mints a
device-registration QR/OTP — that stays in `fsh mobile`.

### Status bar 24px (`shell/Hud.tsx`, element `#vt-hud`)

The bottom edge of the desktop shell, `position: fixed` across the full width, height `--statusbar-h` (24px; 0 in
compact, where the bottom nav takes its place). `body { padding-bottom }`, the sidebar's `bottom`, onboarding's
bottom, the dock viewer and the toast stack (16px above it) use it. Its ids and classes keep the `vt-hud-*` names
(toasts and tests depend on them). The bar itself is `role="region"`; only the message slot is `role="status"`
(`aria-live="polite"`) so chip updates are not announced. Colours: `--color-statusbar-bg` / `--color-statusbar-fg`
with fallbacks to `--bar` / `--sub` (the title bar uses `--color-titlebar-bg`, same fallback).

Left to right: the **agents chip** (`개입 N · 작업 N · 완료 N`; zeros are omitted; with `개입` it is warn-coloured,
ranks 1 and jumps to the longest-waiting `waiting` session via the `agents.jump` action; `완료` counts only
completions not yet seen on this browser) — **pinned leftmost**, outside the layout setting — then the toast
**message slot** (`#vt-hud-msg`), then the left chips (E2E · safe mode · connected screens; `server` is off by
default), a spacer, and the right chips (CPU · GPU · RAM · NET · usage · Notifications (알림) · version · clock). More chip kinds:
**`push`** (`알림 켜짐/꺼짐` for this browser's Web Push subscription, click toggles; hidden where push is
unavailable) and **`clock`** (date + 24-hour time, rebuilt only when the minute changes). Clickable chips are
`role="button"` with Enter/Space.

**Account chip (`account`, `hud-data.ts` `accountChips`).** Shows the current default account and its most-used usage window
(`alice 5h 23% · 3h10m ▾`), right side, ahead of the usage chips; clicking opens the account chooser **above** the chip as a
«set as default» menu (`term/accounts-ui.js` `openAccountMenu({mode:'default', placement:'above'})`, a menu in the Hud chunk, not a core
action). Rules: no registered account (or viewing another Mac) → no chip; accounts but no default → `계정 지정 안 함 ▾`; one tool
with a default → one chip, name only; Claude and Codex both have defaults → two chips, tool name prefixed (`Claude alice …`,
`Codex lab …`). Usage tone is the usage chip's (≥75% warn, ≥90% err; rounded value), priority 4 → 2 when alerting; a default that
is signed out or gone is warn-toned with the reason. Data = `/api/agent-profiles/read/picker` (15 s cache, one call per HUD poll);
the chip refreshes at once on the `vt:accounts-changed` window event that `setDefaultAccount` fires. Choosing the default account
is a **setting**, not part of creating a session: the «+ New session» menu keeps only «Another account, this time only»
(default account shown as its sub text) and the palette's «Change default account» row opens Settings › Agent accounts.

Polled every 30s (CPU/network every 3s, connected screens every 5s), plus immediate refresh on `/ws-notify`
events. A chip with no value is **hidden** (no usage provider → no chip). Reset wording appears in tooltips; GPU
chips show only usage.

**Which chips show, on which side and in what order is the user's choice**
(Settings → Status bar · activity bar (상태 막대 · 활동 막대); device-scoped `ui.hud.layout`, JSON
`{"v":3,"left":[…],"right":[…]}`). The unit is a *kind* (`server`, `screens`, `e2e`, `safe-mode`,
`cpu`, `gpu`, `ram`, `net`, `account`, `usage`, `version`, `push`, `clock`), not a chip id — usage profiles come and go,
so the setting names «usage» once. A kind that is in neither list is hidden, so a new kind
starts off until someone turns it on. The default layout leaves out `server`: seeing the
screen already proves the server is up. The rules (parse, move, step) live in
`shell/hud-layout.js` as plain JS because the settings screen and its test harness cannot
read `.ts`; `hud-data.ts` applies the layout *after* it builds the chips, so «no value hides a
chip» and the squeeze order below still hold wherever a chip is placed. The settings screen
edits the layout three ways — drag, a position select and up/down buttons — all through the same
`moveHudKind`/`stepHudKind` (the select and buttons are the keyboard path, not an extra), shows a
preview with sample values (the status bar as it will look, agents chip first), and is hidden in compact
(there is no status bar there). The settings section is **Status bar · activity bar** (id `hud`). The agents chip is
`PINNED_HUD_KINDS` — shown at the top of Left (왼쪽) with a Pinned (고정) tag, no grip, select or arrows. The saved layout
carries a generation (`{"v":4,…}`): a layout without `v` gets the newer kinds (`push`, `clock`) added once at their
default places when it is read (not an all-off layout, which is a choice); from generation 3 an `agents` entry in
an old layout is dropped on read (it is pinned now); a layout older than generation 4 gets `account` once, just before `usage`
(or at the right end); saving writes `v`.

**"No value" hides a chip; "no room" drops the least important one.** These are
two different axes. The first is `buildHudChips` declining to emit a chip. The
second is `priority` on `HudChip`, a rank for *who survives a squeeze* — the bar
is one 24px line, and a narrow window can easily leave it too narrow
(measured before the status bar spanned the full width: 667px of chips in a 472px bar at a 760px viewport).

The ranking, lowest number survives longest: `server` 1 · alerting usage 2 ·
`screens` >1 3 · `tunnel` 4 · ordinary usage 5 · `screens` ==1 6 · E2E/safe-mode
7 · `version` 8. Two rules generate it — a chip warning you about something
outranks a calm one (so `priority` tracks `tone`, and the same usage gauge moves
between 5 and 2 as it crosses 75%), and a chip whose information is a click away
in the dock can yield before one that has nowhere else to live.

`Hud.tsx`'s `fitChips` does the dropping. Chip widths depend on text, font and
skin, so they can't be known before layout: it renders everything, then while
`scrollWidth > clientWidth` hides the lowest-ranked remaining chip and measures
again. It toggles a class rather than removing nodes — those nodes belong to
Solid's `<For>`, and deleting them behind its back desynchronises it from the
real DOM. It re-runs on chip changes and on a `ResizeObserver` for the bar,
coalesced through `requestAnimationFrame` so dragging the rail measures once a
frame. `overflow-x: auto` stays underneath as the floor for the degenerate case
where even one chip doesn't fit.

### Focus mode — desktop (`layout/focus.js`, `shell/Focus.tsx`, `styles/screens/98-focus.css`)

One switch for both widths: the device setting `ui.focus.active` (**not persisted** — `persist: false` in
`core/settings.js`, memory only, so a reload always starts with the bars) toggles `<html class="vt-focus">`, and
`focus.toggle` is registered **once**, in `layout/focus.js`. Entry points: `focusMode` (⌘. on macOS,
Alt+Shift+. elsewhere — matched on the physical Period key because Shift turns `.` into `>`; user rebinds win), the
title-bar button, the FarShell menu, the palette (`:` keymap row Focus mode (집중 모드), `>` Appearance (모양) › Focus mode) and Settings →
Appearance. On phones (<720) the same switch drives the phone focus mode (§7). **Crossing 720px while on ends focus mode**
(either direction) — the two widths hide different bars, so the other layout never starts without its bars.

- **What hides (≥720).** Only the four *reservations* go to 0 — `--vt-activity-w`, `--vt-side-w`, `--titlebar-h`,
  `--statusbar-h` — so `body` padding changes **once**: each visible pane gets one ResizeObserver report, one fit
  through the refit gate and **one WS `resize`** on entry and one on exit (e2e). The bars keep their own size
  (`--vt-activity-nat` / `--vt-side-nat` in `90-workbench.css`, 36 / 24 fixed), stay `position: fixed`, and fade out
  with `opacity` + `transform` (160ms; `visibility` after). No transition on any width, height or padding. Hidden
  bars get `inert` (`#vt-workbench-slot`, `#vt-titlebar`, `#vt-hud`); keyboard focus inside them moves to the active
  terminal. Pane headers stay (a single pane hides its header as usual).
- **Edge reveal** (`ui.focus.edgeReveal`, default on; hover-capable pointers only). Invisible 8px strips on the
  left, top and bottom (`.vt-focus-strip`; the right edge is the scrollbar's). Resting there for `ui.focus.edgeDelay`
  (`0` / `250` / `500` ms, default 250) adds `vt-reveal-left|top|bottom` to `<html>`: that one bar floats over the
  panes (e2, 180ms; left −12px → 0, top −8 → 0, bottom +8 → 0) and loses `inert`. Padding does not change — **zero
  WS `resize`** (e2e). Left also draws `--color-scrim` behind (click closes); top and bottom do not dim. It closes
  400ms after the pointer leaves the bar (menus and tooltips it opened count as inside; an open menu or a focused
  input keeps it), at once on Esc or when a session row is picked. One side at a time.
- **Needs-you pill** — the same `#vt-focus-pill` element as the phone (`layout/mobile-focus.js`), styled for desktop
  by `98-focus.css`: only while a session waits and `ui.focus.pill` is on; right 12 · top 40 (44 while the title bar
  is revealed) · 28 high · radius 14 · raised · 45% amber border. Left 「Needs you N · name · kind ›」 (개입 N · name · kind ›) goes to the
  longest-waiting session (`oldestWaiting` / `jumpToWaiting` in `focus.js`, the same pick as the status bar's
  `agents.jump`); right 「⌘. End」 (⌘. 끝내기) exits (the shown combo follows the keymap).
- **Entry hint** — bottom centre, 32 high, 3s, "Focus mode · Move the pointer to a screen edge to show the bars · ⌘. End" (집중 모드 · 화면 가장자리에 마우스를 대면 막대가 나타납니다 · ⌘. 끝내기);
  skipped after it has been shown three times on this device (`ui.focus.hintCount`).
- **Touch without hover** (tablets): no edge strips; a 36×36 exit button sits at the bottom left
  (`.vt-focus-exit`), besides the title-bar button and shortcut.
- **Layers.** Strips 505 < scrim 506 < revealed bar 507 < pill · hint · exit 510, all above the onboarding (500) that
  covers an empty window and below menus (520/620), sheets (540) and modals (600+). Toasts rise to 511 in focus mode
  and move 16px above the status bar while it is revealed; the approval dialog, palette and settings open as usual.
- **Lazy.** `layout/focus.js` (core) is the switch, `inert` and the pick of the waiting session; strips, scrim, hint
  and the touch exit button are `shell/Focus.tsx`, loaded from the `shell` chunk the first time focus mode turns on
  at desktop width.

### Resize overlay

While dragging a divider, `142 × 38` (px) + a `cols × rows` caption appears
centered in the active pane (`layout/resize-overlay.js`). xterm's `fit()` is
never called every frame — the refit gate in `layout/surface.js` is what defers fit
during a drag, and the overlay must not defeat that optimization, so it
estimates the cell count per frame from the cell size measured once at drag
start. The overlay fades out 200ms after the drag ends, and under
`prefers-reduced-motion` it disappears immediately with no fade.

### What's left as legacy

Nothing in the DOM. Redesign phase 9 removed the hidden 48px icon rail (`#vt-rail`, 8 buttons), its flyout
(`#vt-rail-panel`, `#vt-rail-settings-tpl`), the 2.0 right usage rail (`#vt-right-rail`) and
`styles/screens/40-rail-legacy.css`. The mic waits in `#vt-mic-park` until the workbench mounts; the earphone
media key and the guide are palette commands. What remains is **read fallbacks** in settings: `ui.rail.*` and
`ui.dock.*` stay in the schema for one release because `shell/side-prefs.js` still reads them when the
`ui.side.*` keys are unset (they are never written). `--menubar-h` survives as an alias of `--titlebar-h`.

## 4. Code viewer / file pane

Where a file opens is the `viewer.surface` setting (Settings → Appearance (모양), per
device):

- **`sheet`** (default) — a card pinned to the top of the terminal area,
  full width, height dragged from a grip at its bottom edge (20–85%).
- **`float`** — a movable, resizable card; header drags it, the corner grip
  resizes it, and it can never be dragged somewhere it can't be grabbed back.
- **`pane`** — a `layout/tree.js` leaf with
  `kind: 'viewer'` that splits the active terminal pane.

`sheet` and `float` are overlays mounted **inside `#terminal-container`**, so
they cover only the terminal area — never the rail, dock, header or mobile
nav — and they don't resize any pane, so the terminal never refits. Clicks
outside the card pass through to the terminal; Esc closes the card only while
focus is inside it (in the terminal it still belongs to vim/less). Opening
another file replaces the content instead of toggling the card closed. The
header carries the file name, a shortened directory (full path in the
tooltip) and a "Open in pane" (페인으로) button (`columns-2` icon) that moves the file into
a side-by-side pane. On compact (< 720px) every surface becomes the
full-screen bottom sheet that closes with the back button. The card reuses
`.vt-viewer-card`'s tokens; its CSS lives in `styles/screens/30-viewer.css`
(legacy layer, next to the card it styles). **Read-only**, with no write API
(`server/fsguard.py`'s three-layer defense: a fixed
root `VT_BROWSE_ROOTS`, `Path.resolve()` + `is_relative_to`, and a filename
denylist).

## 5. Agent state — 5 values (4 observable)

The server decides one state per session; **the frontend only draws it.**

The contract is 5 values — `server/agent_status.py`'s `STATUSES = (IDLE,
WORKING, WAITING, DONE, ERROR)`. Only the first 4 actually have an entry path
— `error` is marked in the same file as *"reserved — no entry path"*. The
color token (`--color-st-error`) and the sort priority have a spot
reserved. So "4 states" means **the states currently observable**, while
the **frontend draws 4**: `error` is not drawn in the UI (no colour, branch, sentence or
group) and any unknown value folds to `idle` (`normalizeState`,
`frontend/js/design/state-classes.js`). The `--color-st-error` token stays — it is
the danger/failure colour for buttons and toasts.

- The component contract is one thing: `.status-dot[data-state="..."]`
  (`styles/layers/components.css`). Color comes from `--color-st-*`.
- **Only `waiting` breathes** — the most valuable state is neither running
  nor done, it's **waiting on your input**.
- The `working` indicator on a card/tab/rail row animates only a **single
  small dot**, never the whole element (`opacity`/`transform` are
  compositor-only, so there's no repaint).
- **One definition of labels and aggregation**, `design/state-classes.js`: labels
  are Idle (유휴) / Working (작업 중) / **Waiting (입력 대기)** / Done (완료) (waiting reads Waiting everywhere). A tab or group is represented by one state with the
  priority `waiting > done(unseen) > working > done(seen) = idle`
  (`representativeState`, shared by the favicon and rail; the view tab does not fold — it draws one segment per session). Among entries of
  one tmux session the server's `_URGENCY` order decides.
- **Unseen** = the state changed and the user has not looked at that session yet — any
  state, not only `done`. Drawn as a 2px-gap ring around the dot (`.status-dot.unseen`)
  or the icon (`.vt-state-icon.unseen`). "Looking" = the session is the focused pane of
  the active view and the window is visible (`document.visibilityState`).
- **State icons** (`ui/icons.js` `stateIcon`, stroke 1.75, no emoji or Unicode check
  marks): idle = empty circle, working = open arc that rotates, waiting = speech bubble
  with a question mark, done = check in a circle. Shape carries the state, not just
  colour. The rail rows and the phone Fleet draw them; the tab bar uses colour segments instead.
- Without the Claude Code hooks (`fsh hooks install`), this value never
  arrives at all — `fsh doctor` and Settings → About surface the gap.

### 5 agent marks

Registered in `frontend/js/ui/icons.js`: a 14px square, `currentColor`, **monochrome
always** (no brand colours).

**Brand logos first.** Where a real logo exists it wins over the drawn
mark: filled paths copied into `frontend/js/ui/brand-icons.js` from
`@lobehub/icons-static-svg` 1.95.1 (MIT) and `simple-icons` 16.33.0 (CC0) — never loaded
from a CDN at runtime. That file is its own lazy chunk (`brand.js`, kept out of the
initial-payload budget); until it arrives the stroke mark below is drawn, then
`vt:brand-icons` repaints tabs, cards, rail and fleet. Brand logos:
claude, codex, agy (Antigravity), qwen, ollama, lms (LM Studio), python,
node, docker, opencode, cursor-agent, copilot, amp, goose, kimi. The stroke marks
(stroke 1.75) remain for aider, llama.cpp, ssh, shell and unknown names, and as the
pre-load fallback.

| Key | Shape | For |
|---|---|---|
| `agent-claude` | Spark (4-point star) | claude |
| `agent-codex` | Bracket `[ ]` | codex |
| `agent-aider` | Two overlapping circles (pair programming) | aider |
| `agent-agy` | Two overlapping diamonds ("twins") | Antigravity CLI, `agy` |
| `agent-qwen` | Two triangles apex-to-apex (bowtie) | qwen-code CLI, `qwen` (an independent coding-agent CLI, not lumped into `agent-local`) |
| `agent-local` | Chip (square + 4 pins) | local LLM runners detected via raw foreground command: `ollama`, `lms` (LM Studio), `llama-cli` (llama.cpp) — all three share this one mark, labels differ |
| `agent-ssh` | Globe (circle + meridians) | remote SSH sessions |
| `agent-python` | Two joined coils | plain `python`/`python3`/`Python` foreground process, not an AI CLI |
| `agent-node` | Hexagon | plain `node` foreground process |
| `agent-docker` | Stacked squares | plain `docker` foreground process |
| `agent-shell` | Prompt `>_` | plain shell (no agent), unknown names |

## 6. Settings · palette

### Settings (`Mod+,` / FarShell menu)

**Window (mock O04–O06, redesign phase 9):** 1240×788 (88vh cap), radius 8, `--color-bg-raised`, `--shadow-e2`.
Head 44: gear 16 + Settings (설정) 15/600, the auto-save status on the right (12, muted). Left nav 220 on `bg-1` with
the search field (28) on top; items 32 high, 13px, radius 6, the current section on the selection tint
(`acc-soft`) in 600. Content padding 20/28, left-aligned (max 760): title 22/600, description 13 muted, group
heads 12/600 muted 20 above, rows padded 12/0 with a 1px line below, label 13/500, help 12 muted, scope tag
11/500 muted with a 1px `line-strong` border and no fill. The right preview column is 300 wide, padded 20, on
`bg-1` (terminal and appearance sections only; hidden below 1100px). Appearance adds a **focus mode preview**
under the skin preview (mock O04-B): a 170px static sketch — two panes in terminal colours, the Needs you 1 (개입 1) pill
and the accent edge bands (left 4, top/bottom 3) — that follows Show bars at screen edges (가장자리에 막대 띄우기) · Needs-you pill (개입 표시) · Edge reveal delay
(가장자리 반응 시간) and explains them in the caption; it never turns focus mode on. Status bar · activity bar (상태 막대 · 활동 막대) (O05) lays its three zones
side by side above 720px with 32px item tiles and puts Activity bar order (활동 막대 순서) in the right column; Account and security (계정 및 보안) (O06) draws the device table with a 28 head and
48 rows, this device on the selection tint.

**Account (`accounts`, `settings/sections/accounts.js`).** Between Agents and Keymap, desktop and phone alike (on a phone it is
the only place to change the default account — there is no status bar). One group per tool (`Claude 기본 계정`, `Codex 기본 계정`),
one standard `.vt-set-row` per profile: name + a `기본` tag on the default (or the sign-in state tag when not `ok`), help lines
for usage · reset (toned warn/err by the chip's thresholds) and source · updated-at, and a `.vt-btn.sm` on the right (`기본으로 지정`,
`해제` on the default; disabled with the reason when the profile is not usable). Last group «Add accounts» holds the
«register only your own accounts» note and a link to the run-profile manager (MCP window › 실행 프로필, `showMcp('profiles')`). Values are
the server's picker list as is — unknown stays «알 수 없음», nothing is auto-picked.

**Three settings, three icons.** Settings (설정) = gear (reached from the FarShell menu, `Mod+,` and the activity bar's bottom gear), Rail settings (세션 목록 설정) = `sliders-horizontal` (rail header), Status bar settings (상태 막대 설정) = `panel-top` (FarShell menu item).

Settings are split across two scopes — `frontend/js/core/settings.js`'s
`SCHEMA` gives each key a `scope: 'global' | 'device'` (default `global`).

- **global**: `/api/workspace.settings` (server) — a value changed on the
  phone shows up on the Mac browser too. Keymap, skin, autocopy, scrollback
  chunk count, etc.
- **device**: `GET/PUT /api/device-settings` — the server identifies the
  device via the `vt_device` cookie and stores it at
  `~/.vt/device-settings/<device_id>.json` (0600). Values that only make
  sense "on this device": sidebar width, activity, collapse and pinned tool
  (`ui.side.*`), terminal font
  size, whether mouse events forward to the app, the mobile home tab
  (`ui.mobile.home`). Split off because a pinch-zoom on the phone must not
  grow the Mac terminal's font.

Key bindings are rebindable, and **`passthrough` hands a key back to the
terminal** — that's how you get `Mod+F` back as the shell's `forward-char`.
Turning off "forward mouse events to the app" in the mouse section makes
drag-select always work even under vim/tmux mouse mode.

**Compact-viewport rules for settings screens:**

- **Wide tables scroll inside a wrapper.** Wrap them in
  `.vt-set-covtable-host` (`overflow-x:auto`), as `agents.js`'s coverage table
  and `security.js`'s device table (Device (기기)/id/Registered (등록)/Last used (마지막 사용)) do; otherwise the
  columns run off the right edge on a phone.
- **Switches and segments come from one builder.** `panels/settings/controls.js`
  (`switchButton`, `segmentedControl`, `choiceControl`) is the only place that makes them; sections never draw their own.
  On/off is a `.vt-switch` (`<button role="switch" aria-checked>`, track 28×16, 40×24 on touch with a 52×44 hit area);
  a choice of three or fewer short options (each ≤10 characters, ≤24 in total) is a `.vt-segmented`
  (`role="radiogroup"` of `role="radio"` buttons, roving tabindex, arrow keys select; 28 high, 44 on touch); anything longer
  stays a `<select>`. Hover/pressed use `--color-state-hover/press`, the focus ring is the shared `:focus-visible` rule, and
  `scripts/check_contrast.py` checks the on-track (`--color-acc-surface` ≥3:1 on every surface), the off knob and the
  selected/unselected segment for all 9 skins. `frontend/tests/settings-controls-contract.test.js` fails when a section draws
  an old ON | OFF pill (`.vt-toggle`), a native checkbox, or a short select. To go back, change the class the builders emit.
  Switches do not stretch: the compact `.vt-set-row { flex-direction:column; align-items:stretch }` would turn one into a bar,
  so rows containing one use the wrapping row layout instead.
- **Range slider thumbs are 20px under the compact media query** (both
  `::-webkit-slider-thumb` and `::-moz-range-thumb`). The desktop affordance
  is `:hover { transform:scale(1.15) }`, which never fires on touch, so the
  knob would stay a tiny 12px.
- **MCP · Skills · Plugins (MCP · 스킬 · 플러그인) window (mock P09-b, `panels/agent-config.js`).** The window keeps its size
  (1240×860) and the legacy view's enlarged text; only the unified manager's insides follow P09-b: a top bar
  (Management context (관리 대상) as a segmented control, Host (호스트)/Sessions (세션) selects with an inline 12px label, Refresh (새로고침) right), a 200px
  left nav on `bg-1` (icon 16 · 13px label · mono 11 count, current = `bg-3` + 600), a sticky filter bar
  (28px controls on `bg-2`), the bulk bar, rows with a 56px head (checkbox · caret · name mono 13/500 + kind
  tag 16 + tool/host/scope in 12 muted, source path mono 11 ellipsized · state, `st-working` when on · Turn on (켜기)/Turn off (끄기)
  24) and a footer bar (help left, restart and Existing MCP library and advanced settings (기존 MCP 보관함·상세 관리) right). Two columns only in the
  wide modal; phones and the dock keep one column with the nav as a scrolling strip. The rules live
  unlayered in `components.css` next to the base `.vt-ac-*` rules — in the legacy screens layer they would
  lose to them regardless of specificity. Opening the legacy view drops `.vt-agent-config` so its 20px
  padding returns.
- **MCP server rows' Korean explanatory text uses the UI font**
  (`.vt-mcp-meta`/`.vt-mcp-verify`, `--ui`). Hangul in a monospace face has
  uneven glyph spacing and reads worse; a lone identifier inside a sentence
  doesn't need its own font to be legible.
- **MCP server row buttons (On (켬)/Off (끔), Verify (검증)) are 32px tall** on compact, not the
  desktop `.vt-chip.sm` 20px. 44px would overlap the adjacent tag chips (only a
  12px gap — the same crowding tradeoff as the terminal-selection handles in
  § 7), so 32px applies only to the two primary-action chips, not the tag
  chips that are meant to stay small and packed.

### Command palette (`Mod+K`, `shell/Palette.tsx`)

One palette covers quick open, commands and in-pane search. 8 prefixes:

| Prefix | Mode | Source |
|---|---|---|
| (none) | `default` | Recent commands (2) → recent files (2) → every command, grouped and scrollable — **no sessions** (see below) |
| `/` | `file` | File fuzzy search (`GET /api/fs/search?q=`, debounced) |
| `?` | `grep` | File **content** search (`GET /api/fs/grep`) — the palette widens to 1100×720: options row (⌥C/⌥W/⌥R toggles, include/exclude globs, scope), progress row with Cancel (취소), results grouped by file with the match in `--color-term-hl`, a 460px preview of the selected line. The same screen is the sidebar Files › Search contents (파일 › 내용 검색) (`shell/GrepView.tsx`, `styles/screens/97-grep.css`) |
| `@` | `session` | Sessions — the *only* place sessions are listed in the palette |
| `:` | `keymap` | Actions **that have a key binding** (the `core/keymap.js` registry, 14) — shows the current binding next to each, so a rebind shows up here too. Commands without a binding (Sign out, Clear cache, Update settings…) are not listed; find them without a prefix. The empty-result line says so |
| `#` | `queue` | Prompt queue items |
| `!` | `port` | Open ports |
| `~` | `scrollback` | Server-side scrollback search (`GET /api/search/scrollback?q=` — greps the full session ring buffer) |
| `>` | `settings` | Settings (theme, push, etc. — the home of the settings that have no other entry point) |

Two-column layout — left: result list, right: a **preview** (a file shows a
highlighted excerpt of its start, a session shows its last lines + state, a
scrollback result shows surrounding context). The preview hides on narrow
widths. `⌘F` opens the palette's `~` mode; the in-pane search bar
is on `⌥⇧F` (rebindable); `⌘⇧F` opens the sidebar's content search.

### Default screen

The no-prefix screen lists no sessions (session switching lives in the
Sessions (세션) tab, and `@` is the only palette path) and the command list covers
everything: `settings.show`/`clients.show` (no activity pairs with these, so they are in
`DEFAULT_COMMANDS` **without** `isPanel: true`, which keeps
`rail-palette-parity.test.js`'s check from firing on them; every tool activity's action — `scm.show` …
`system.open` — **is** `isPanel`, and that test compares them with `shell/activity-data.ts`) plus every
non-`unavailable` `core/keymap.js` action, so the palette is a real "search
every command" surface and not just files+a fixed list.

Order on the empty-query screen: 2 most-recently-used commands → 2 most-
recent files → everything else, grouped by kind (existing
`sectionHeadAt`/`KIND_META` machinery, no new grouping code needed). Recency
is tracked by **action id** (`Row.key`, e.g. `cmd:settings.show`), not label —
a `__toggle:` command's label never changes but its target does, so id is the
only stable identity — in the device-scoped setting
`palette.recentCommands` (JSON array, newest first, capped at 10, read/written
through `window.vtSettingsGet/Set` since `shell/Palette.tsx` can't statically
import `core/settings.js`). Typing a query drops the recent/files
split and just flattens everything that matches — recency ordering only
matters when you're browsing, not searching.

**Every `ACTIONS` entry needs a `register()` call.** `core/keymap.js`'s
dispatcher silently no-ops when `_handlers.get(id)` is `undefined`, so an
action defined with a label and default binding but never registered does
nothing when pressed. `viewer` (⌃⇧E) delegates to the existing `viewer.show` action
(`panels/viewer-lazy.js`). `paste` (⌃⇧V) is registered in `term/keymap-actions.js`:
native paste shortcuts use the browser `paste` event when available, then a
delayed Clipboard API fallback; palette invocation and other rebound shortcuts
use `pasteFromClipboard(activeSessionId())` directly.

**Scroll clipping:** the palette's results container carries the class
`vt-qo-body`, which only *looks* like the code viewer's `vt-vw-body` — they
don't share a rule, so `vt-vw-body{flex:1 1 auto; overflow:auto}` does not
apply to it. Without its own rule the div has no `flex-grow` (it stays at
content size inside the column-flex card) and no `overflow:auto`, and the
outer `.vt-viewer-card` clips at `max-height` with `overflow:hidden`, so
results past that height are unreachable — a hard cut, not a disabled
scrollbar. `styles/screens/30-viewer.css` therefore sets
`.vt-qo-body{ flex:1 1 auto; min-height:0; overflow:auto; }`.

**Mobile is a full-height bottom sheet** (`@media (max-width:719.98px)`,
mirroring the existing file-overlay `vt-file-compact` pattern) rather than the
60vh-capped card — the scroll region needs real height to be usable with a
thumb, and 60vh suits a handful of file-search rows, not a "browse
every command" list.

## 7. Mobile (compact, < 720px)

Redesign phase 8 (2026-11) restyled the whole compact layout to the "tidy" (정돈) mock
(`specimen/final/mobile.html`, M01–M14) with the phase-3 tokens. Its rules live in one file,
`styles/screens/98-mobile.css` (after the skeleton in `60-mobile-hud.css` and the bars in
`95-bars.css`, before the overlays in `99-overlays.css`). Sizes: app bar 52 · bottom nav 52 ·
session card 64 (two lines) · answer button 44 · keybar 90 (keys 44×36, gap 6, padding 6) ·
floating mic 56 · sheet item 44 · focus pill 28 (touch 44) · top edge 24. Surfaces: bars
`--color-bg-1`, cards and sheets `--color-bg-raised`, inputs `--color-bg-2`; amber
(`--color-st-waiting`) is only for Needs attention (개입 필요) (card border and strip, the answer-key numbers, the
Session-tab badge).

```
┌ app bar 52 ── session name ▾          [focus] [keybar] [{}] [search] ┐
│               MacBook Pro M1 Pro · n/N                             │
│                                                                     │
│   tab content (session / terminal / changes / files / settings)    │
│                                                        ( mic 56 )   │
├ keybar (terminal tab, opened from the app bar) — Ctrl Alt Shift │ ↑ ┤
│      Esc Tab ⇧Tab ^C Home … F12 ▸ (horizontal scroll)     │ ← ↓ →  │
├ bottom nav 52 ── session | terminal | changes | files | settings ──┤
└─────────────────────────────────────────────────────────────────  ┘
```

**The header is the same on every tab** (it never changes per bottom-nav tab,
so the same screen real estate always means the same thing). It holds three
things: an **env chip** (`#vt-env-chip`, `layout/env-chip.js`) reading `machine` (falling back
to `hostname`) off `/api/capabilities` (`"MacBook Pro M1 Pro"`, no OS prefix; machine is macOS-only,
read once via `system_profiler` and cached for the process lifetime, same
pattern as the `hostname`/`version` fields), the session picker
(`#voice-session-picker`, a display that opens the rename/close/
kill sheet; the session tab below has full parity with it),
and the command-palette search icon. The workspace chip (repo name + host) is
desktop-only; a phone doesn't need to see the repo name on every screen.
There is no desktop tab strip on a phone; the mobile tab bar shows a single
view like any other.

Bottom nav has **5 tabs** (`frontend/index.html`'s `#vt-mnav-bottom`):
**Session · Terminal · Changes · Files · Settings**. Queue has no slot in
the bar but is reachable from the command palette's `#` mode. Files (attachments)
has a slot and shows a badge
of in-flight upload jobs (`window.vtUploadJobs()`, event-driven off
`vt:upload-progress` rather than polled). Settings opens straight into
Settings, whose own first screen already lists those as "Tools". Each
full-screen panel stays inside the mobile navigation shell and Android/browser
Back closes that panel first. There's no title bar, status bar or second status row: waiting
count is a badge on the Session tab. When the software keyboard opens, the
bottom nav hides and the keybar moves down to the keyboard edge so both bars
do not consume the same vertical space — and `#terminal-container`'s bottom
padding accounts for the 52px bottom nav in addition to the keybar, otherwise the
prompt line and tmux status line hide behind the keybar whenever the nav is showing.

**Fleet home** (`shell/Fleet.tsx`, labelled "Sessions (세션)" on the bottom nav) is
the first screen (`ui.mobile.home` defaults to `fleet`) — most of what you do
on a phone in compact is "what's waiting on me right now," not typing. Its
data source is the same `/api/tmux/sessions` + `allSessions()` join as the desktop rail's local path
(`Rail.tsx`), with the **same rows and zones**:
`collectSessionRows` (`rail-fetch.ts`) builds them, `buildRailLists` (`rail-data.ts`) splits
them into Needs attention (개입 필요) and Sessions (세션) (name order), and the row parts (state icon + unseen ring,
keyword line, origin icon) are `RailRow.tsx`'s own components. Only the waiting rows in the
Needs attention zone carry the inline approval buttons (the same session also appears below
without them). Unseen is shown by the ring on the state icon, not a separate badge.

**Session cards** (`FleetRow` in `Fleet.tsx`) are two lines, 64px including a 1px border:
line 1 state icon 16 (unseen ring) · agent mark · name 15/600 · delegation toggle · elapsed
(mono 12, right); line 2, indented 24, keyword 12/600 (with tool/badge) · location (WT ·
directory/branch, mono) · changes · glyphs (share, other screens, origin) on the right. A waiting
row in the Needs attention (개입 필요) zone that is open becomes a card: raised surface, amber border at 42% and a
3px amber strip on the left, the question on one line in terminal colors, and 44px answer
buttons with the key drawn apart in amber (`AnswerButtons` `fleet` variant: 「1 예」; the
desktop rail keeps 「1 · 예」). The same session lower down in Running (실행 중) is a plain row. A top
row carries Conversation archive (대화 보관함) and New session (새 세션) as 44px icon buttons; zone heads are 36px.

**Long-press a row for a bottom-sheet menu** — the same items as the desktop ⋯ menu
(`rowMenuItems`: open rows get Close (닫기) · Rename (이름 변경) · Open on Mac (맥에서 열기) · Connected screens (연결된 화면), closed rows get
Open (열기); tmux sessions add Kill (종료), red), the same window bridges as the desktop rail
(`runRowMenu`), just a sheet instead of a coordinate popup — a touch menu
that opens where you pressed can walk off-screen. Detection is a plain
pointer-event timer (500ms / 8px cancel, touch-only — mouse is left to the
desktop's ⋯ button) since `layout/dnd.js`'s long-press is wired to actual
drag-and-drop and isn't reusable as-is. **Two pitfalls:** the sheet's action callbacks must capture the row into a
plain variable before calling `closeMenu()`; reading the render-prop's resolved
value (`m()`) afterwards finds the `<Show>` block already torn down, so the
action silently does nothing from a real tap while working when called from a
console. And the sheet is `Portal`ed into `body` with its backdrop at `z-index:655`
(`60-mobile-hud.css`): inside `#vt-fleet-slot` (a stacking context at 500) no `z-index` can
get above the 650 bottom nav, so the sheet's last row (Cancel) sat behind the nav and
touches went to the nav (2026-11-10, measured with `elementFromPoint`). Like the specimen's
M04, the scrim covers the nav — the sheet needs no nav-height padding, only the safe-area.
Header rows of the bottom-tab screens (Changes, Files/Attachments, Settings) share one
16px + safe-area side gutter (`98-mobile.css`), and the sheet and its title are
`user-select:none` (the rename input excepted) so a long-press cannot leave a text
selection on the sheet title.

A permanent **"+ New session (새 세션)"** button sits at the bottom of the list (hidden
only in the true empty state, where two dedicated buttons already do the
same job) and opens the same `session.add-menu` popover as the desktop rail's
own "+ New session" — `Fleet.tsx` takes `getAction` as a dep (from
`core/dom.js`, passed in by `mobile-nav.js`) for this, the same way
`Rail.tsx`/`Hud.tsx`/`Dock.tsx` already do.

**Text selection (`term/touch-select.js`) does not use the
browser's native selection at all.** The reason: xterm 5.5's accessibility layer redraws every visible row
on each PTY render (a spinner, a tmux status-line clock — anything), and that
redraw replaces the row's text nodes, which collapses whatever DOM `Range`
was sitting on them. A drag-selected range surviving long enough to copy is
close to accidental. So long-pressing the terminal selects the word under your finger through
**xterm's own selection model** (`term.select()`/`getSelection()`/
`clearSelection()`, the same API a desktop mouse-drag uses), which a
separate renderer paints and which the accessibility layer's churn never
touches. Two self-drawn handles (teardrops, `.vt-term-select-handles`) sit at
the range's ends; dragging either recomputes a flat `row*cols+col` index pair
and calls `select()` again — no live DOM `Range` is ever created, so there's
nothing for a redraw to collapse. Measured: selecting text, then having the pane print five lines over five seconds
(forcing the accessibility redraw repeatedly), leaves `term.getSelection()`
byte-for-byte unchanged.

`keybar.js`'s `setTerminalSelectionMode` owns entry/exit (blur the
hidden textarea, hide the software keyboard, toggle the
`vt-term-select-mode` class, show/hide the Copy (복사)·Select all (모두 선택)·Done (완료) bar); a long-press on the terminal
triggers it, and handle-driven `select()` calls (not a native range) run inside it. "Select all" selects the visible
viewport (`rows×cols`), not `selectAll()`'s full scrollback — buffer text you
can't see isn't what "select all" means to someone looking at a phone screen.

**Auto-copy and the Android long-press-to-paste fallback are disabled on touch**
(`term/selection.js`) — `mouse.autocopyOnSelect` governs desktop drag-select, but on a coarse
pointer neither path runs, regardless of the setting. Otherwise a drag would copy without being
asked and a long-press could fire the OS clipboard-read permission prompt and paste
before the user had decided to select anything; both belong to a native-selection model that
touch does not use.

**Handle dragging must be verified on real touch hardware**, not only CDP
emulation (a physical Android phone over `adb` with Chrome remote debugging, driving real
`Input.dispatchTouchEvent` calls and `elementFromPoint`/computed-style inspection).
xterm's `AccessibilityManager` sets an **inline** `style="pointer-events:auto"` on
`.xterm-accessibility` (the vendor CSS's `.xterm-accessibility:not(.debug){pointer-events:none}`
never wins against an inline style) with `z-index:10`. `.vt-term-select-handles` therefore
needs `z-index:11` (above it; `.vt-term-wrap` establishes no stacking context of its own);
at `z-index:5` the accessibility layer's hidden row `<div>`s win every hit-test over the
terminal text, so handle-drag receives no `pointerdown`/`pointermove` events (its listener is
attached directly to `.vt-term-handle`) even though long-press-to-select still works (that
listener is on the wrapper and only needs the event to bubble through). Known open item: the
very first long-press's selection is confirmed by `term.getSelection()` immediately, but the
blue highlight overlay sometimes doesn't paint until a second selection-changing action (e.g. the
first handle-drag movement) — copying and dragging both work regardless, so this is cosmetic,
not functional, and unexplained.

**The keybar (`term/keybar.js`)** has two left rows (Ctrl·Alt·Shift pinned
on top, a horizontal scroll strip below) and inverted-T arrows on the right. The strip
(`#keybar-scroll`, `touch-action:pan-x`) fires on **release**, and
cancels past an 8px slop or on the browser's `pointercancel` when it takes
over the scroll — firing on `pointerdown` would press whatever key the finger landed on
while swiping, and would leave no way to hold a modifier while pressing another key. The arrow cluster
(`#keybar-arrows`) does not scroll and fires on press. Ctrl/Alt/Shift share one model — tap = next key only,
tap again = lock, held = applies while held (to keybar keys and soft-keyboard
input alike), and a held modifier that was used in a chord does not count as
a tap on release. The strip carries only keys the soft keyboard lacks (Esc
Tab ⇧Tab ^C Home End PgUp PgDn Del tmux prefix F1–F12); there are no character keys, long-press
variants or collapse toggle. The mic is not a keybar key except in focus mode (below), where it
becomes the first key (`#keybar-mic-slot`).
Modifiers sit outside the strip so a chord is always in the same place and a thumb that
drifts while holding one does not cancel it. Modifier state reads like a
Caps Lock light: a 5px lamp top-right — faint ring (off), accent outline +
ring (next key), filled key + solid lamp (locked). On compact the same state is also spelled out to the right of
Shift (`#keybar-mod-hint`, mock M02): "Ctrl next key only" (Ctrl 다음 키만), "Ctrl locked" (Ctrl 잠김), both joined with 「 · 」 when mixed, nothing
while a modifier is only held. The slot is always there (`flex:1`, one line, ellipsis, `title` carries the full text)
and reaches 50px into the empty cell left of ↑, so the text appearing or going never moves a key. Keycaps use `--mono`. The
strip fades whichever edge has more keys (`.more-left`/`.more-right`) and
maps vertical wheel to horizontal.

When subscribing to `core/settings.js` changes, check the key is present in the
notification before acting: `set()` broadcasts only `{ [key]: value }` for the one
key that changed. A listener that reads an absent key as `false` (for example when
switching mobile tabs persists `ui.mobile.home`) would act on a wrong value and
**write it back to the device's stored settings**.

**The app bar** has the session name as the one focal element
(600 weight, `--fs-md`, no button chrome — the ▾ says it's pressable); the
device and position are a mono caption under it ("MacBook Pro M1 Pro ·
2/5"), which absorbs the separate `#vt-env-chip` (`layout/env-chip.js`
only publishes `html[data-vt-machine]` + a `vt:machine` event for
`picker.js`). The bar is 52px on compact; the rule is written as
`:root, html[data-skin]` because the farshell/macos skin blocks pin
`--topbar-h:32px` at `html[data-skin=…]` specificity, which silently beats a
bare `:root` (the coarse-pointer 44px rule loses to it on those
skins). 44px icon buttons on the right (icons 20): on the terminal tab
`#vt-focus-btn` (focus mode, first), `#keybar-open-btn` (keyboard) and
`#snippets-open-btn`; on every tab search. The keybar is **closed by default** and only that button opens it —
it never opens itself when the soft keyboard appears; the
state is the device-scoped `keybar.open`. Closing also releases any latched
modifier, and text-selection mode still shows its copy/done row while
closed. Touch leaves `:hover` stuck after a tap, which would make a closed toggle
look pressed — on `(hover:none)` only `aria-pressed` paints it.

**File editing on compact** (`layout/mobile-edit.js`, mock files M-F5). While a viewer pane is in edit mode
(`panels/viewer/edit.js` puts `vt-vw-editing` on the pane and fires `vt:viewer-edit` on start and stop),
`html.vt-mobile-editing` hides the bottom nav and shows `#vt-edit-keys` at the bottom: Tab ← → ↑ ↓ Esc and an accent
Save (저장), keys 40×32 in the keybar key style. The row follows the soft keyboard with `visualViewport` (same formula as
`keybar-ui.js` `positionBar`), and the compact file sheet's bottom moves up by the row height plus the keyboard overlap
(`--vt-edit-keys-h`, `--vt-edit-overlap`), so the cursor line and Save are never under the keyboard. Keys send a
`keydown` to the editor's `.cm-content` (CodeMirror's own keymap: Tab indents, arrows move, Esc collapses the
selection); Save clicks the bar's `.vt-vw-save` (one save path, disabled together). `pointerdown` is prevented so the
editor keeps focus. A dialog on top (save elevation, discard) hides the row. Stopping the edit, the pane going away
(watched on its ancestors), or crossing 720px restores the nav.

**Settings detail on compact** (mock M08). The section intro becomes a 48px sticky row (‹ 44 + title, the description
hidden); rows are 56 high; rows whose control is small (switch, select, number) put it to the right of the label and wrap
it below only when the label would get narrower than 160px; switches draw as a 40×24 track with a knob (still
`role=switch`/`aria-checked`); number rows hide the slider and keep the input with its two 44px steps. Wide controls
(presets, key combos, tables) stay under the label. CSS only (`98-mobile.css`).

**Login OTP step** (mock M10). Six 52px boxes, one digit each (`#login-otp-boxes`, `role=group` labelled Verification code (인증 코드),
each box "Verification code digit n" (인증 코드 n번째 자리), `inputmode=numeric`; the first box carries `autocomplete=one-time-code`). Typing moves to
the next box, Backspace in an empty box clears the previous one, ←/→ move, and a paste or autofill of several digits fills
from that box (six digits always from the first). The submitted value is still the single hidden `#login-otp`
(`gate.js` joins the boxes), so the error flows (`otp_invalid` clears the boxes and focuses the first, `otp_locked`,
short code) are unchanged.

**Quick open is a full-height bottom sheet on compact** instead
of the 60vh-capped card shared with the desktop layout — see § 6's "Default
screen" for the details (sessions kept out of the default
screen, recent-commands tracking, the keymap registration rule, and the
`vt-qo-body`/`vt-vw-body` scroll-clipping pitfall).

**Soft keyboard.** Two rules, both driven by `visualViewport` in
`term/keybar.js` `positionBar` (so they follow the keyboard consistently with
`--kb-height`/`--kb-overlap`):
- *Bottom nav hides while the keyboard is open* (`body.vt-keyboard-open
  #vt-mnav-bottom`, `60-mobile-hud.css`) — **intentional**, not a bug: the
  keybar sits on the keyboard and the terminal needs the height; the nav
  returns when the keyboard goes away. The same class swaps the terminal
  bottom padding to the nav-less value.
- *Focus mode* — when the **visible height** (`visualViewport.height`) is under
  300px (landscape phone + keyboard: ~400px minus ~200px), `body.vt-kb-focus`
  hides the tab bar, pane headers, the workbench, the pinned sidebar, the status bar and bottom nav
  (`20-overlay.css`) and releases the workbench body padding, so the terminal
  takes the whole remaining height. The keybar stays (it is closed by default
  anyway). No state is kept: the same function removes the class when the height
  comes back. Pointer-fine devices never get it (the keybar is not wired there).
  If a session is waiting, the focus pill (below) shows in this mode too, without
  its exit button.

**Floating mic (`#vt-mic-fab`).** Under 720px the workbench (and its activity-bar mic)
is hidden, so the phone had no visible mic. `term/keybar.js` `placeMicButton` moves the
`#mic-btn-wrap` node itself (voice.js caches that id; recording state and the
`data-voice-state` dot come along) into `#vt-mic-fab` on compact widths, into
`#keybar-mic-slot` in focus mode, and back to the activity bar on wider screens; it
re-runs on the width media change and on `vt:focus-change`, and leaves the mic alone
while voice-only mode has it in `#topbar`. The button is 56px, accent fill, `--shadow-e2`,
right 16, and sits 16px above the bottom nav — or above the keybar when it is open:
`bottom: nav + --kb-height + --kb-overlap + 16` (the nav term drops when the soft keyboard
hides the nav). Terminal tab only; hidden while selecting text. The voice panel opens as a
bottom sheet above the nav, over the mic.

**Focus mode (manual, `layout/mobile-focus.js`).** Entered **only** from the app-bar
button (user decision 5 — no two-finger double tap; it clashes with pinch zoom). It sets
`html.vt-focus` (the same switch, class and `focus.toggle` action as desktop focus mode — `layout/focus.js`
owns them, this file only paints the button, the pill and the pull-down edge) and hides the app bar,
bottom nav and session slot. The keybar stays at the bottom and is shown even when closed
(its toggle is gone with the app bar); the mic is its first key. The terminal gets 40px of top
padding for the **pill**: top right, 28px high with 44px touch targets, 「Needs you N name」 (개입 N name) in amber
when a session waits (tap → the longest-waiting session, `jumpToWaiting`) plus an exit button. Exit
also by pulling down from the **top 24px edge** (`#vt-focus-edge`, handle 36×4): a hint follows
the finger and reads Release to exit focus mode (놓으면 집중 모드를 끝냅니다) past 64px; releasing past 64px exits,
less snaps back. Leaving the terminal tab or crossing 720px (either way) also exits. Not persisted.

## 8. Usage (사용량, activity bar)

Two interfaces in `server/usage/base.py`:

```python
class LimitProvider:    # limit-based — remaining fraction
    def read(self) -> list[LimitGauge]     # {label, pct, resetsAt, window}
class CounterProvider:  # counter-based — cumulative, no limit
    def read(self, since) -> list[Counter] # {label, tokens, seconds, tokPerSec, samples}
```

- **Limit-based**: `usage/clauth.py` — reads clauth's `~/.clauth/status.json`
  (the same source clauth uses). Only limit-based gauges appear on the HUD.
- **Counter-based**: `usage/counter_jsonl.py` — reads
  `~/.vt/usage-counter.jsonl`. Populated by `fsh usage add --model ...
  --tokens ... --seconds ...` (CLI) or `POST /api/usage/counter` — which
  script actually makes that call is up to the user; FarShell only stores
  and displays. `usage/ollama.py` (optional) surfaces the running local
  model as a counter label whenever `localhost:11434/api/ps` responds.
- If neither provider exists, the Usage (사용량) activity disappears entirely — `VT_USAGE_PROVIDER` (`auto`/`clauth`/`none`) controls the choice. There is no always-on usage badge or right usage rail any more (removed in redesign phase 9 — both drew into hidden DOM).

## 9. xterm.js terminal theme

The heart of "feels like iTerm2 vs. feels like Windows" isn't the window
chrome, it's **the terminal's own background + the 16 ANSI colors**.
`js/theme.js`'s `VT_XTERM_THEMES` defines a complete palette per skin:
`background/foreground/cursor/selection + black..white + brightBlack..brightWhite`.

- farshell: #121316 (the pane background) + blue cursor (#7aa2ff); ANSI colors are the redesign's terminal palette (§2 "Terminal colors")
- macos: deep black (#101012) + macOS system colors (red #ff453a, green #32d74b, blue #0a84ff, …)
- catppuccin: #1e1e2e + the Catppuccin Mocha palette
- windows: the **official Campbell palette** (#0c0c0c, red #c50f1f, blue #0037da, …)
- vscode: #1e1e1e + VS Code's integrated-terminal default palette
- notepad: the only light background (#fffefb) + blue cursor (#0060df)

Each skin's palette is also the source of its `--color-term-*` tokens (§2), so previews
outside xterm use the same colors. Redesign phase 3 changed a few ANSI values that were
under the contrast bar: `macos` brightBlack #5a5a60→#626268, `catppuccin` brightBlack
#585b70→#6c7086 (Mocha overlay0), `vscode` brightBlack #666666→#6b6b6b, `windows`
brightMagenta #b4009e→#e000c4, `notepad` yellow #b08800→#917000 and cyan #0598bc→#047e9c.

`addSession()` (`js/term/xterm-setup.js`) applies `getVtXtermTheme()` at
creation time, and on skin switch `setVtSkin()` (`js/theme.js`) updates
`term.options.theme` on **every open terminal** immediately.

## 10. Component rules

- **Rows**: only three heights, 24/28/36. Text is `--font-ui` 12px; numbers,
  paths, and times are `--font-mono` 10.5~11px.
- **Badges** (`.vt-badge`): 1px `--color-line` border, no fill, `--radius-sm`.
  Only status badges get a status-colored border. A badge **counts** something —
  a diff count, an unread count, a port count.
  **A badge needs room of its own.** It sits in the flow, so it widens whatever
  holds it: an 18px `min-width` badge inside a 36px fixed strip makes the
  strip 38px wide and `overflow: hidden` cuts the rest off (the activity bar therefore places its
  badge `absolute` on the icon corner). Where the container is
  a fixed strip, drop the number for a 6px `--color-acc` dot positioned
  `absolute` (contributing 0px of width) and let the tooltip say the count.
- **Tooltips** (`.vt-tip`, `js/ui/tooltip.js`, loaded on first hover/focus or idle by
  `ui/tooltip-lazy.js`; touch never loads it): one `position: fixed` element on
  `body`, driven by `data-tip` (name) and optional `data-tip-sub` (the value —
  a count, a shortcut, a reason; `--font-mono`, `tabular-nums`, `--color-sub`; stacked under the name at the same x).
  `data-tip-side` picks `top|bottom|left|right` and flips when the side doesn't
  fit the viewport **or the trigger's panel** (§ "Interaction state rules"). **Never the native `title` attribute**: its ~1s delay can't be tuned, it
  can't be themed across the 9 skins, and it does nothing under touch. 400ms to
  open, instant within 300ms of the last one closing, 120ms
  opacity+scale growing from the trigger. No arrow — an arrow has to track both
  the fill and the 1px edge, and always drifts in one skin.
  A tooltip is a *description*, not a name: icon-only controls still carry
  `aria-label`, which is also the only label a touch device ever gets.
- **Toasts** (`.vt-toast` / `#vt-hud-msg`, `js/ui/toast.js`): a card is 340 wide on the raised
  surface, padding 12/12/12/14, radius 8 — [16px icon] [head: message · `×N` pill] [muted detail
  line] [up to two action buttons] [× close] (redesign phase 6, mock O16-D). It keeps the 2px
  leading status bar (an inset `box-shadow`, never `border-left`, which the radius would clip)
  and the 5 status colors; color goes on the icon and the bar only — the message stays body
  text, so three stacked cards stay readable. `showToast(msg, type, { detail, actions })` adds the
  second line and buttons; a toast with actions always becomes a card (the 24px status-bar slot has
  no room for a button). The status-bar slot is a tinted chip that hugs its text (severity colour at
  10% with a 25% hairline), not a bar across the strip.
  **Where it lands is decided by severity, because the goal is to not cover the
  terminal.** `info`/`success` take the status bar's message slot (`#vt-hud-msg`, right of the
  pinned agents chip) for a moment — it covers no terminal rows, like tmux's status line or vim's echo area; the
  other chips step aside while it shows, the agents chip stays. Only `warn`/`error` float,
  as a card at the bottom right 16px above the status bar, clear of the prompt line at the
  bottom left. In compact (<720px) there is no status bar, so everything is a card,
  sitting on the **measured** height of the keybar and bottom nav
  (`--kb-height`/`--kb-overlap`) and following the software keyboard up.
  Translucency is not an answer here: behind a toast is monospace text, so a
  see-through card makes both layers unreadable, and `backdrop-filter` over the
  constantly repainting xterm canvas re-blurs every frame.
  **Say a success only when the screen doesn't already show it.** A deleted row
  disappears, an inserted path is typed into the terminal, a closed selection bar
  is its own receipt — none of those need a toast. Work that leaves no trace on
  screen (a clipboard copy) is confirmed **at the trigger**: `okFlash()`
  (`js/ui/okflash.js`) swaps the button's icon for a check for 900ms inside the
  same 16px box, so nothing shifts. One action never fires two toasts — issuing
  and copying a share link is one sentence, and a progress message is replaced
  in place by its outcome through a shared `key`.
  At most 3 cards at once; past that the oldest goes, but an `error`/`warn`
  outlives an `info` — letting information evict a failure inverts severity.
  Tapping dismisses; hovering (fine pointer) holds the timer. Default duration is
  3s for `info`/`success`, 6s for `warn`/`error` — a success confirms something
  already seen, a failure has to be read.
- **Overlays** (redesign phase 6, mock `overlays.html`; rules in `styles/screens/99-overlays.css`): every
  floating window is one shell — `--color-bg-raised`, 1px `--color-line-strong`, radius 8, `--shadow-e2`,
  behind it `--color-scrim`; head 44 (the "tidy" (정돈) dialogs — approval, voice — 48), body padding 16 (20),
  footer row with a top hairline and 12/16 padding. **At most one filled button per window**, and none on
  a window that grants or destroys: the approval window (O11/O11-B) puts Deny (거부) left with default focus
  and "Once · For this session · Later" (한 번만 · 이 세션 동안 · 나중에) right, all unfilled, and keeps its 400ms input guard; a danger
  confirm (O16-G) focuses Cancel (취소) and outlines the danger action. The voice panel (O16-F, 440) opens beside
  the activity-bar mic anchored to its bottom edge so it grows upward; Input (입력) is its only filled button and
  Stop recording (녹음 종료) is a danger outline. Session previews (palette `@`, empty-pane picker cards) draw the session's
  screen in `--color-term-*` through `lib/ansi-spans.js` — text as `textContent`, colour as one of 14
  `ta-*` classes, never inline style. No blur anywhere here (the disconnect overlay used to blur).
- **Tags** (`.vt-tag`): a filled faint tint, no border, `--radius-sm`. A
  different species from `.vt-badge` — a tag doesn't count, it states a
  *property* (public exposure, trust tier, "this device", a plan name like
  `Pro`/`rolling`). The tint is derived from `currentColor`
  (`color-mix(… 16%, transparent)`), so a consumer sets only `color` and the
  ratio isn't restated at each use site. Not a pill — the whole screen is one
  5px radius step. Use this one component rather than copying the `color-mix`.
- **Tabs (inside panels and dialogs)**: active = a 2px `--color-acc` bottom underline +
  `--color-acc-surface` background. Inactive = `--color-sub` text.
- **Panes** (`.vt-pane`): flush, like iTerm2. No border, no radius, no `gap`
  between siblings, no padding on `#terminal-container` — the boundary is the
  1px `--color-line` divider and nothing else. `--term` background and
  `overflow: hidden` stay (the terminal lives inside the pane, so without the
  clip it spills over its neighbour while a pane shrinks).
  Do not add a bordered, gutter-separated box to make xterm's
  cell-quantization remainder read as the pane's own padding: it *adds* 5px of
  gutter to the empty strip it was meant to explain. The remainder is not the kind
  of thing you fix by adding space.
- **Pane sizes snap to whole terminal cells** (`layout/snap.js`). A split pins
  its **first** child to a multiple of the cell (`flex: 0 0 568px`) and lets
  the second take the rest; both can't be multiples at once, because
  `multiple + 1px divider + multiple` isn't one. What that buys is that the
  leftover stops appearing at *every* boundary and collects at one outer edge
  instead. Vertical snapping measures from below the 24px head, so a pane is
  `head + rows × cellHeight`. Dragging a divider therefore moves in whole
  columns. Without snapping, a half-drawn column shows up as a blank strip
  between the last glyph and the divider.
- **The active pane says so through its head**, three ways at once: a 10%
  accent-tinted surface, a 2px accent underline, and a `--color-txt` name
  (inactive: `--color-sub`). The pane itself can't carry the signal — with no
  gutter, two neighbours each drawing a border would stack three 1px lines at
  the divider. The underline is an inset `box-shadow`, never a thicker
  `border-bottom`: a border would eat terminal height, so every focus change
  would refit and send a `SIGWINCH`.
  **Which pane is active is decided by focus, never by a click** — the pane
  listens for `focusin` (xterm's hidden textarea bubbles it up). A click
  would let you make a viewer pane "active" while the keys still go to a
  terminal, which is the exact mismatch the rule exists to prevent.
- **Pane head buttons hide until needed**: `opacity: 0` unless the pane is
  hovered, active, or the button has `:focus-visible`. Four panes means
  twelve icons and at most one of them is wanted. Always visible under a
  coarse pointer — there is no hover to reveal them with.
- **Buttons**: default = outline only. The primary action (commit, issue
  link, create-and-switch) = filled `--color-acc` + `--color-on-solid` text.
  **One filled button per screen.**
- **Toggles**: a segment that reads as **text** first (`[ON][OFF]`), never a
  switch. The active segment gets an accent border.
- **Inputs**: `--color-bg-0` background, `--color-line` border,
  `--color-line-strong` on focus. No glow.
- **Dividers**: 1px line, `line-strong` on hover, accent while dragging.
- **Agent-share surfaces** (`.vt-srow-share`, `.vt-share-*` in `components.css`): a security state is never colour alone — the
  shared mark pairs shape (eye = read, pen = write), a word (Write (쓰기)) and colour (`--color-st-waiting` for write only; read is
  neutral). The warning copy is plain type hierarchy (first line semibold), no box, no icon. The approval modal is a decision
  prompt: its first-focus button is the **safe** one (Deny), no button is filled/accent (neither answer should look like the
  obvious one), buttons ignore input for 400 ms after drawing, and Esc is Later (나중에), never an answer. 44px under a coarse pointer.

## 11. Accessibility

Session lists use one editable grouped-view header above pane-order members, including remembered closed views.
The group title is separate from session names and Git metadata; `이름 변경` is a text action with a 44px
touch target. Rows show current directory and branch as text rather than a main/worktree SVG. State/unseen,
agent identity, sharing permissions and connected-screen marks retain their distinct meanings.
See [session UI design](docs/ref/session-ui.md).

- **Touch targets:** under a coarse pointer, the bottom nav and keybar
  buttons keep a 44px tap area. A fine pointer keeps a tighter density.
  The **pane-header ×** (`.vt-pane-close`)
  keeps its visible 18px box and 13px glyph and gets a 44x44 hit area only under
  `(pointer: coarse)`: 13px transparent padding with `background-clip: content-box`
  (hover/active still paints 18px). Padding, not a `::before`, because a scrolling
  container (`overflow-x:auto`) clips a pseudo-element vertically and padding takes layout space,
  so it cannot overlap a neighbour; the pane header grows to 44px (the
  header's right padding is 10px to clear the split resizer's 8px touch zone). On a
  fine pointer it stays 18px / 24px-header.
- **Keyboard:** `Mod+K` palette, `Mod+,` settings, `Ctrl/Cmd+F` (palette `~`
  mode), `Alt+Shift+F` (in-pane search), `Cmd+Shift+F` / `Ctrl+Shift+G` (file content search), Esc to close,
  `:focus-visible` outline (`--color-focus-ring`, one rule — § "Interaction state rules").
- **Screen readers:** icon buttons carry `aria-label`; mic status is
  `role="status" aria-live="polite"`. A visible tooltip puts
  `aria-describedby="vt-tip"` on its trigger and removes it on close.
- **`prefers-reduced-motion`:** globally disables animation/transition;
  `waiting` falls back to an outline.
- **safe-area-inset:** padding applied on top/bottom/left (notch, gesture bar).

## 12. File map

The full module map lives in [ARCHITECTURE.md](./ARCHITECTURE.md) §2. This
table is deliberately not exhaustive — only files tied directly to *visual*
design.

| File | Responsibility |
|------|------|
| `styles/theme/tokens.css` | Structural tokens + farshell color tokens + shell dimensions (`@theme static`) |
| `styles/theme/skins.css` | Color remapping for the other 8 skins |
| `styles/layers/components.css` | `.status-dot` — the home for new components |
| `styles/screens/*.css` | Per-screen CSS in the `legacy` layer (wins over `components`) |
| `frontend/index.html` | Boot theme script (anti-FOUC), login gate, `#vt-workbench-slot` mount point, mic parking slot, mobile bottom nav, keybar |
| `frontend/js/layout/surface.js` | Terminal placement (which pane holds which session) · refit gate · `#vt-term-stage` waiting room |
| `frontend/js/core/types.ts` | Shared TS types |
| `frontend/js/shell/Workbench.tsx` · `side-prefs.js` | Workbench: activity bar + one shared-width sidebar + the right pinned sidebar; width/collapse/activity/pin rules |
| `frontend/js/shell/ActivityBar.tsx` · `activity-data.ts` | Activity bar 48px (vertical tablist) and its items |
| `frontend/js/shell/Rail.tsx` | Worktree rail — the sidebar's sessions view |
| `frontend/js/shell/Dock.tsx` | Dock body — the sidebar's tool view (`vtDockHost`), pinned-panel hosting and the head pin button |
| `styles/screens/90-workbench.css` | Activity bar, sidebar, right pinned sidebar frames and their width variables |
| `frontend/js/ui/icons-shell.js` | Icons only the workbench draws (lazy `shell` chunk) |
| `frontend/js/shell/TitleBar.tsx` | Title bar 36px: FarShell menu · host · breadcrumb · palette field · tools |
| `frontend/js/shell/Hud.tsx` · `hud-data.ts` · `hud-layout.js` | Status bar 24px at the bottom; chip catalogue and layout rules |
| `styles/screens/95-bars.css` | Title bar and status bar frames, chips, FarShell menu dropdown |
| `styles/screens/99-overlays.css` | Overlay shell (dialogs, approval, work record, archive, picker, sheets, state screens) and terminal-colour session previews (`.vt-ansi`, `lib/ansi-spans.js`) |
| `frontend/js/shell/Fleet.tsx` | Mobile fleet home; `FleetRow` is the two-line 64px session card |
| `styles/screens/98-mobile.css` | Phone (compact) look: app bar, bottom nav, cards, sheets, keybar, floating mic, focus mode |
| `frontend/js/layout/mobile-focus.js` | Phone focus mode: app-bar button, top-edge pull to exit; the needs-you pill shared with desktop |
| `frontend/js/layout/focus.js` | The one focus-mode switch (`ui.focus.active`, `focus.toggle`, `focusMode`), `inert`, width-change exit, longest-waiting pick |
| `frontend/js/shell/Focus.tsx` | Desktop focus mode: edge strips and reveal, scrim, entry hint, touch exit button (lazy `shell` chunk) |
| `styles/screens/98-focus.css` | Desktop focus mode: reservations to 0, hidden/revealed bars, strips, desktop pill, layers |
| `frontend/js/shell/Palette.tsx` · `palette-data.ts` | Command palette, 8 modes |
| `frontend/js/layout/resize-overlay.js` | px / cols×rows caption while dragging a divider (lazy `resize-overlay` chunk) |
| `frontend/js/layout/tree.js` | Pane tree — leaf `kind: 'terminal'\|'viewer'\|'panel'` |
| `frontend/js/layout/breakpoints.js` | The 4 responsive-tier boundaries (single source) |
| `frontend/js/layout/dom-ids.js` | Tree node id → DOM element id, in one place (the maker and the finder both read it) |
| `frontend/js/layout/snap.js` | Cell-multiple snapping for pane sizes — pure, unit-tested |
| `frontend/js/layout/panes.js` · `compact.js` · `dnd.js` | Pane-tree rect computation · compact mode · drop zones |
| `frontend/js/core/settings.js` | Server-backed settings store — global/device scope |
| `frontend/js/core/keymap.js` | Keymap registry |
| `frontend/js/panels/viewer/scm.js` | Changes (변경, source control) tool renderer |
| `frontend/js/ui/icons.js` | Inline SVG icon registry (the only icon source) — includes the 5 agent marks |
| `frontend/js/ui/tooltip.js` · `tooltip-lazy.js` | The one tooltip layer — `data-tip` / `data-tip-sub` / `data-tip-side`, replaces native `title` (body in the lazy `tooltip` chunk) |
| `frontend/js/term/xterm-setup.js` | xterm instance creation, applies `getVtXtermTheme()` |
| `server/usage/base.py` · `clauth.py` · `counter_jsonl.py` · `ollama.py` | LimitProvider/CounterProvider |
| `server/device_settings.py` | Device-scoped settings storage |
| `frontend/js/voice/` | Recording/STT/TTS, media keys, voice-only mode — built as its **own** lib entry (`frontend/dist/voice.js`), lazy-loaded only when the voice capability is on |
