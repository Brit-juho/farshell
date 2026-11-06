# FarShell Design System


Visual rules, tokens and component guidance for FarShell. See
[ARCHITECTURE.md](./ARCHITECTURE.md) for the module map and
[CHANGELOG.md](./CHANGELOG.md) for release history.

---

## 0. Design principles

These are the rules this project actually holds itself to. Read this before
drawing a new screen.

### Restraint is taste

Gradients, animations, glassmorphism, decorative icons — none of them go in
just because they *can*. Every visual element has to earn its place, and the
default is always the simpler thing.

### Color — 70/20/10

Four neutral background steps (`--color-bg-0..3`) are the 70%, four text
steps (txt/sub/muted/faint) are the 20%, and the accent is the 10%.

- **One solid accent per screen — the primary action.** If the active rail
  item, a badge, and a primary button all fill with the accent at once, none
  of them reads as primary.
  → The active rail/dock item states itself through background elevation;
  the accent survives only as the status bar's color and the icon color.
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
  acrylic), and only on the header bar. The default skin `farshell`'s cards,
  panels, dock, and rail are all opaque.
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

- [ ] No icon trapped in a color box
- [ ] No emoji used as a design element
- [ ] No default serif on a hero
- [ ] No unrequested glassmorphism
- [ ] No gradient on text or buttons
- [ ] No color glow behind a button
- [ ] Accent respects 70/20/10 (one solid accent = the primary action)
- [ ] No purposeless card nesting
- [ ] No slow entrance animation on content
- [ ] Row heights stay within 24/28/36
- [ ] No `border-left` + `border-radius` together
      (the bar gets clipped at the corner — a rail status bar uses a
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
| `farshell` | Near-black (#08090a) + **single amber accent** (#f0a860). Not an impersonation of any OS — our own face | ✅ default |
| `macos` | iTerm2/Terminal.app — traffic lights, SF fonts, system blue, translucent header bar | |
| `catppuccin` | Pastel — no chrome, lavender accent | |
| `windows` | Windows Terminal — caption buttons, Cascadia, Fluent blue, square UI (`--radius-sm` overridden to 2px) | |
| `vscode` | VS Code integrated terminal — dark gray, Fluent blue accent, square UI (`--radius-sm` 2px) | |
| `notepad` | Notepad/paper feel — the only **light theme**, warm off-white background | |
| `warp` | Warp terminal — near-black with a violet cast, single-token accent (`--color-acc`) drawn from a coral→lavender→sky gradient family that surface CSS (e.g. the settings page title) can paint as a gradient text-fill; rounder corners (`--wrad` 16px) than any other skin | |
| `linear` | Linear — near-black, one flat indigo accent, almost-invisible hairline borders (`--color-line` alpha .05, half of every other dark skin) | |
| `vercel` | Vercel/Geist — pure black/white + one blue signal color, hard edges (`--wrad`/`--trad` 2px, sharpest of all 9) | |

`warp`, `linear` and `vercel` are modelled on real products in FarShell's own
category (a terminal app and two dev-tool dashboards) and intentionally depart
from the restraint language in §0. Every value (backgrounds, the 4-step text
ramp, the accent, status colors) is solved algorithmically against
`scripts/check_contrast.py`'s thresholds rather than picked by eye: pick
values, compute contrast, adjust until passing.

Switched from the header's ⚙ settings, or the theme chip in the command
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

The contract is **21 colors** (4 background + 2 divider + 4 text + 6 accent +
5 status), plus 2 terminal colors handed to xterm and 1 surface-state color
that ride alongside. The corner radius has one step (there is no
`--radius-md`).

| Token | Value | Use |
|------|-----|------|
| `--color-bg-0` | `#08090a` | Deepest background |
| `--color-bg-1` | `#0e1011` | Chrome (header, rail, dock, HUD) |
| `--color-bg-2` | `#0b0d0e` | Row headers, subbars — **darker than `bg-1`.** A subbar has to sit one notch below the chrome itself to read as a "subheading laid on a row" (the order is not monotonically increasing) |
| `--color-bg-3` | `#141719` | Hover / active row — also the raised surface an active rail row sits on |
| `--color-line` / `--color-line-strong` | `rgb(255 255 255 / .07)` / `.14` | Two divider steps |
| `--color-txt` / `--color-sub` / `--color-muted` / `--color-faint` | `#e8e9ea` `#8e9296` `#73787c` `#5d6164` | Four text steps (`faint` is for disabled labels, placeholders and decorative numbers — never body text) |
| `--color-acc` | `var(--acc-farshell)` = `#f0a860` | The accent. Kept, together with the 5 status colors, **for those two roles only** |
| `--color-acc-dim` | `#b3803f` | Badge border, outline |
| `--color-acc-surface` | `var(--color-acc)` (skin-overridable) | The surface under `acc-ink` text (primary buttons, active-tab underline background). Defaults to the accent itself — **only `macos` overrides it to `acc-dim`**, since white text on Apple's system blue measured 3.65:1 |
| `--color-acc-ink` | `#17140f` | Text on top of the accent |
| `--acc-farshell/macos/catppuccin/windows/vscode/notepad` | Each skin's own accent hex | **Out-of-scope constants.** The theme chip needs to show the accent dot of "a different skin than the one active now," so these live outside `data-skin`'s scoping alongside the scoped `--color-acc` — they're the single source both the chip dot and each skin's `--color-acc` read |
| `--color-st-{idle,working,waiting,done,error}` | `#7c7c84` `#4fd1a5` `#f0a860` `#5b8def` `#f06868` | The 5 agent states (§5) |
| `--color-term-bg` / `--color-term-fg` | `#0a0a0b` / `#eeeef0` | xterm background / foreground |
| `--color-surface-active` | `rgb(255 255 255 / .18)` | The "currently selected" surface (rail, fleet home rows). Twice the hover overlay's alpha — the same value would be indistinguishable from hover on a pointer device. The accessibility requirement (WCAG 1.4.11) is carried by the row's left 3px status bar, not by this |
| `--color-st-waiting-tint` (+ `--waiting-tint-pct: 10%`) | `color-mix(--color-st-waiting 10%, transparent)` | Background of an attention-needed row (`.vt-srow.is-waiting`, Fleet). Used instead of `--color-surface-active`, which reads as a pressed grey "inactive" row on the light skin. On this tint the row's secondary text rises from muted to sub and the amber keyword to txt; `scripts/check_contrast.py` and `tokens-contrast.test.js` check txt/sub >= 4.5:1 on the tint over bg-0/1/2 in all 9 skins (the percentage is the ceiling — windows' yellow waiting colour leaves sub at 4.69:1) |
| `--color-on-solid` | `#ffffff` | Fixed white text over status banners, etc. |

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

The text bars are checked on `bg-0`, `bg-1` and `bg-2` (`bg-3` is the
hover/active row, where text is only briefly seen).

**Measured (worst case across 9 skins x `bg-0..2`, from
`python3 scripts/check_contrast.py --table`):**

| Skin | `txt` | `sub` | `muted` | `faint` |
|---|---|---|---|---|
| farshell | 15.69 | 6.09 | 4.28 | 3.05 |
| macos | 11.20 | 6.09 | 4.28 | 3.05 |
| catppuccin | 8.69 | 6.29 | 4.29 | 3.06 |
| windows | 10.84 | 6.11 | 4.29 | 3.08 |
| vscode | 9.54 | 6.07 | 4.26 | 3.09 |
| notepad | 11.53 | 6.10 | 4.30 | 3.06 |
| warp | 16.34 | 7.07 | 4.45 | 3.33 |
| linear | 17.27 | 6.71 | 4.45 | 3.23 |
| vercel | 19.80 | 7.57 | 4.61 | 3.30 |

This table is recomputed on every CI run by
`frontend/tests/tokens-contrast.test.js` — you don't need to hand-update this
document every time a token value changes, but if the table drifts far
enough, refresh it.

### Structural tokens

- **Spacing:** a single `--spacing: 4px` scale. Every margin is
  `calc(var(--spacing) * N)`.
- **Font size:** 11 steps, `--fs-3xs`(10.5px) through `--fs-4xl`(46px); `--fs-base` is 15px.
- **Radius:** `--radius-sm`(**5px**, every control) / `--radius-lg`(**10px**,
  outer frames, sheets) / `--radius-full`. There is no `--radius-md`; use `sm`. The `windows` and `vscode`
  skins override `--radius-sm` to 2px inside the skin for their square UI.
- **Shadow:** `--shadow-e1` (the current row) + `--shadow-e2`/`e3` (overlays).
  e1 must be defined in every skin — see §0 for why.
- **Motion:** `--dur-fast`(120ms) / `--dur-base`(200ms), `--ease-out-quint`.
- **Breakpoints:** `--breakpoint-regular`(720px) / `--breakpoint-wide`(1280px) /
  `--breakpoint-xwide`(1600px). The JS-side constants live in exactly **one**
  place, `frontend/js/layout/breakpoints.js` (`COMPACT_MAX`/`REGULAR_MAX`/`WIDE_MAX`).
- **Shell dimensions:** menu bar 28px (top, full width; the only header at >=720px) · worktree rail default 252px (resizable
  240~480) · dock default 392px (320~560, collapses to 36px). The
  rail's and dock's width/collapse state are **device-scoped settings**
  (`ui.rail.*`/`ui.dock.*`, §6 "Settings") — collapsing it on a phone must not
  affect the Mac.

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
| `rail-items` | `8` | `frontend/index.html` — count of `.vt-rail-btn` (**the legacy 48px icon rail `#vt-rail`, hidden via CSS** — see §3. The shell's worktree rail `#vt-wgrail` is not counted here). `dir` is counted alongside `files` |
| `agent-states` | `5` | `server/agent_status.py` — `STATUSES` (`error` is reserved, §5) |
| `color-bg-0` | `#08090a` | `styles/theme/tokens.css` |
| `color-acc` | `#f0a860` | `styles/theme/tokens.css` |
| `color-txt` | `#e8e9ea` | `styles/theme/tokens.css` |

### Legacy aliases

`--win`/`--bar`/`--acc`/`--txt`/`--sub`/`--line`/`--ok`/`--err`/`--crust`/
`--trad` etc. in `styles/screens/00-tokens-reset.css` are aliases pointing at the `--color-*` tokens
above. Bulk-replacing the hundreds of consumers with `var(--color-*)` is a
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
┌ menu bar 28 (full width, fixed) ───────────────────────────────────────┐
│ ◆FarShell ▾ [workspace chip] [agents]…  [CPU RAM NET …] [v] [clock] [⌘K] │
├──────────┬───────────────────────────────────────────┬───────────────┤
│ rail 252 │ pane tree                                  │ dock 392      │
│(worktree)│ ┌ pane header 24 ┐                         │ ┌ tabs 28 ┐   │
│          │ │ cwd · agent     │                        │ │scm|q|…  │   │
│          │ └─────────────────┘                        │ └─────────┘  │
└──────────┴───────────────────────────────────────────┴───────────────┘
```

### 4 responsive tiers

| Tier | Width | Rail | Dock | Pane cap | Rendering |
|---|---|---|---|---|---|
| compact | < 720px | none (fleet home, §7) | a tab in the bottom nav | 2 | 1 pane fullscreen, swipe left/right |
| regular | 720~1279px | `#vt-wgrail`, device-scoped width | collapsed to 36px by default, opens as overlay | 4 | split tree |
| wide | 1280~1599px | `#vt-wgrail`, device-scoped width | collapsed to 36px by default, opens by pushing | 6 | split tree + rail |
| xwide | ≥ 1600px | same | 392px, open by default | **unlimited** | same as wide + cap lifted |

> **The rail is never the legacy icon rail.** Regular does not fall back to the
> 48px `#vt-rail`: `#vt-rail` is
> `display:none` at every width (see "What's left as legacy"). The worktree rail
> is the only rail, at every tier.

The tier touches the rail in exactly two places, and **neither overwrites what
you chose** — `ui.rail.width`/`ui.rail.collapsed` are device-scoped settings
(§6), and a stored value always wins.

- **Collapse default** (`defaultRailCollapsed`): on a device that has never
  stated a preference, the rail starts collapsed below wide (<1280). `Dock.tsx`
  makes the same call (`innerWidth < WIDE_MAX`), so a narrow screen does not open
  with both panels expanded and leave the menu bar 472px for 667px of chips.
- **Rendered width clamp**: `--vt-wgrail-w` is `min(<stored>px, 40vw)`. The
  stored value says how wide you want it; the clamp asks whether the window can
  spare that. Shrink the window and a 480px rail draws at 40vw; widen it and it
  returns to 480 — the setting is never rewritten, so a wide rail survives a
  temporary narrow window. 40vw can't drop below the rail's own 240px minimum,
  because under 720px there is no rail at all (compact).

Because the variable holds a `min()` expression rather than a plain px
number, the resizer reads the element's measured width instead of parsing it —
a custom property doesn't resolve to computed px, so `parseInt` would return
`NaN` and the rail would jump on drag.

The rail's and dock's open/width state are **device-scoped settings**
(`ui.rail.collapsed`, `ui.rail.width`, `ui.dock.collapsed`, `ui.dock.width`) —
collapsing it on a phone must not carry over to the Mac. The tier boundaries
live in exactly one place, `layout/breakpoints.js`. Past the cap, the split
button goes disabled **with the reason in its tooltip**.

### View tabs

**There is no tab strip.** The rail switches between sessions and grouped views (clicking a row goes to
that view and focuses its pane), names a group (the group header's 편집) and closes, so a strip would be a second copy
of the same navigation. There is no `#vt-wtabs`, per-tab state bar or tooltip, right-click tab menu or drag-to-reorder. The **view model** remains:
a view is still one tab in `layout/store.js` that owns one pane tree, because grouped layouts, a viewer/panel pane that
belongs to a session and the remembered closed groups are all stored per view (docs/ref/terminal.md「Session and view
rules」). There are no next/previous-view or move-view keys either; view order changes only in the phone's order sheet. Think of a view as just a group (or a session with its viewer panes), not as something you navigate.

At >=720px `#topbar` is `display: none` (`--topbar-h: 0`); the traffic lights (macOS skin), the keyboard toggle
(touch devices), `⌘K` and the Windows caption buttons move into the menu bar (`#vt-mb-lead` / `#vt-mb-tools`,
`layout/menubar-slots.js` — they are **moved**, not copied, so ids and listeners survive) and move back below 720px.
Voice-only mode keeps `#topbar` because it turns it into the full-screen container. Dragging a rail row onto a pane
still uses `layout/dnd.js`.

### Worktree rail 252px (`#vt-wgrail`, `shell/Rail.tsx`)

A row is **one tmux session** (docs/ref/terminal.md 「Rail」). Two zones: **개입 필요**
(waiting sessions; collapsible, and switchable on/off in 레일 설정 like VS Code's
Open Editors) and **실행 중** (every other running session, **name order only** — no urgency
sorting, so rows never jump). Below them, **최근 종료** (collapsible, newest N) and a one-line
**보관함** that swaps the list in place; ended rows use the same three lines with a dashed state
circle and a dimmed name, and only 이어서 시작 is accent-filled. There are no 묶음 / 묶지 않음 / 잠자는 중 zones. A row is three lines:

1. **State icon** (`stateIcon`; a ring around it = unseen) · agent mark · name (edited in place).
   A small window icon at the line's end = "open in this browser tab".
2. **Main/worktree icon** (`git-commit` = main worktree, `git-branch` = linked worktree) ·
   branch — **main is shown too** — · change size (`+add −del`, or `파일 N`).
3. **State keyword** · elapsed time · **origin icon** (local has none; `laptop` = created on
   another Mac, `square-terminal` = CLI / plain tmux).

Only when applicable, a fourth line: the grouped view's name and a 「다른 화면 N」 badge
(tmux clients attached beyond this browser tab's own). The state keyword is short and fixed so
it is never truncated: 승인 요청 / 선택 요청 / 계획 확인 / 질문 for waiting (server `kind`),
유휴 / 작업 중 (+ tool name, the one place allowed to ellipsize) / 완료 (+ 「질문 있음」 /
「계획 있음」 from the server `badge`, which working rows show too — agy's plan badge rides a working entry). There is no left 3px status bar, repo color bar, tmux plug icon or `? question` line — state is the icon's colour **and** shape.
A session that is not open keeps full opacity but its name steps down to `--color-sub`.

The ⋯ button and right-click open the same menu (`rowMenuItems`: 열기/닫기 · 이름 변경 ·
맥에서 열기 · 연결된 화면 · 종료). The gear-less sliders icon in the rail header is **레일 설정** (개입 필요 on/off, show
or hide sessions by origin — peer / external, and the 저장소 표시 list). Hiding an
origin never hides a session that is open or waiting. The rail's width resizer spans
240~480px. Collapsed (48px), only the state icon and agent mark remain; name, branch and
keyword move to the row tooltip.

### Dock 392px (`shell/Dock.tsx`)

Fixed to the right, collapses to a 36px strip via a chevron (no bottom dock, to keep
terminal vertical space). 5 tabs: **Source control · Queue ·
Files · Ports · Usage** (later tabs — attachments, system and **Snippets**
(`braces` icon, action `snippets.show`) — follow the same frame rules).

**Collapsed, a tab is an icon — never rotated text.** Do not rotate the
36px strip's labels with `writing-mode: vertical-rl`: it works for Latin
(glyphs lie on their side) but not for CJK, where Korean syllables *stack*, so
「소스컨트롤」 becomes a five-character tower, in a monospace face. Each tab
carries an icon from `js/ui/icons.js` (`git-branch · list · folder · plug ·
gauge`), shown only while collapsed; the full label stays in `data-tip`. The
expanded strip is text-only on purpose — five tabs in a row with icons as well
is crowded. The frame owns only tab switching, collapse, the width resizer
(320~560), and device-scoped persistence — each tab's content is drawn by the
existing panel renderers (`queue.js`, `ports.js`, `panels/usage.js`,
`panels/viewer/scm.js`). There are no modal popup panels; the action ids
(`queue.show`, `ports.show`, `usage.open`, `scm.show`) open a dock tab (the
palette and keymap reference those same ids). The ports and usage tabs
disappear entirely when `/api/capabilities` reports no source for them.

**The source-control tab supports status, diff, stage, unstage and commit.**
Push and PR/MR creation are not offered. Commits use the repository's ambient Git
configuration; the stored git accounts are not connected to this workflow.
See [docs/ref/worktree.md](./docs/ref/worktree.md) for the guards.

**The code viewer is separate from the dock file tree.** Files open in the configured
`sheet`, `float` or `pane` surface; compact screens use a fullscreen sheet.
See [docs/ref/files.md](./docs/ref/files.md). In `layout/tree.js`, a pane leaf carries `kind: 'terminal' | 'viewer' | 'panel'`. A `viewer` leaf has
only a file path; a `panel` leaf has one registered dock panel name. Both keep
`session: null`.

### Menu bar 28px (`shell/Hud.tsx`, element `#vt-hud`)

The menu bar is a macOS-style bar at the top of the screen. Its ids and
classes keep the `vt-hud-*` names (toasts and tests depend on them). It is `position: fixed`
across the full width at the very top, so the rail, dock and body start `--menubar-h` (28px) lower
(`.vt-sidepanel { top: var(--menubar-h) }`, `body { padding-top }`); anything positioned from the top
(`--topbar-popup-y`, onboarding, the dock viewer) adds `--menubar-h` too. Look: UI font, no chip
dividers, a translucent blurred bar, hover pills — shared by every skin (colours come from skin tokens).
Compact (<720px) has no menu bar, so `--menubar-h` is 0 there.

Left to right: the **FarShell menu** (`MenuButton`, first cell: 새 세션 · 빠른 열기 · 접속 주소 복사 ·
설정 · 메뉴바 설정; the dropdown is a `Portal` into `body` because the bar clips overflow and its
`backdrop-filter` would trap `position: fixed`; Esc/outside click/↑↓/Home/End work), the workspace chip
(`#vt-workspace-chip-slot`, moved here from the tab row), then the chips below. **접속 주소 복사** copies
the public tunnel URL if one is running, else `location.origin`; it never mints a device-registration QR/OTP
— that stays in `fsh mobile`.

More chip kinds: **`agents`** (`개입 N · 작업 N · 완료 N`; zeros are omitted; with `개입` it is
warn-coloured, ranks 1 and jumps to the longest-waiting `waiting` session via the `agents.jump` action;
`완료` counts only completions not yet seen on this browser), **`push`** (`알림 켜짐/꺼짐` for this browser's
Web Push subscription, click toggles; hidden where push is unavailable) and **`clock`** (date + 24-hour
time, rebuilt only when the minute changes).

A row of chips like `screens 3 · E2E ON · safe mode ON · CPU 17% · GPU 0% · RAM 72% ·
NET ↓12K ↑3K · Claude 62% · 4h12m · v2.6.0`. Polled every 30s, plus
immediate refresh on `/ws-notify` events. A chip with no value is **hidden**
(no usage provider → no chip). There is no menu bar at all in
compact — the bottom nav takes its place. Chip padding is 6px horizontally,
with 3px internal gaps. Reset wording appears in tooltips; GPU chips show only usage.

**Which chips show, on which side and in what order is the user's choice**
(Settings → Status bar; device-scoped `ui.hud.layout`, JSON
`{"left":[…],"right":[…]}`). The unit is a *kind* (`server`, `screens`, `e2e`, `safe-mode`,
`cpu`, `gpu`, `ram`, `net`, `usage`, `version`), not a chip id — usage profiles come and go,
so the setting names «usage» once. A kind that is in neither list is hidden, so a new kind
starts off until someone turns it on. The default layout leaves out `server`: seeing the
screen already proves the server is up. The rules (parse, move, step) live in
`shell/hud-layout.js` as plain JS because the settings screen and its test harness cannot
read `.ts`; `hud-data.ts` applies the layout *after* it builds the chips, so «no value hides a
chip» and the squeeze order below still hold wherever a chip is placed. The settings screen
edits the layout three ways — drag, a position select and up/down buttons — all through the same
`moveHudKind`/`stepHudKind` (the select and buttons are the keyboard path, not an extra), shows a
preview with sample values, and is hidden in compact (there is no menu bar there). The settings section is
**메뉴바** (id `hud`). The saved layout carries a generation (`{"v":2,…}`): a layout without `v`
gets the newer kinds added once at their default places when it is read (not an all-off layout, which
is a choice); saving writes `v`.

**"No value" hides a chip; "no room" drops the least important one.** These are
two different axes. The first is `buildHudChips` declining to emit a chip. The
second is `priority` on `HudChip`, a rank for *who survives a squeeze* — the bar
is one 24px line, and the rail plus dock can easily leave it too narrow
(measured: 667px of chips in a 472px bar at a 760px viewport).

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

### Resize overlay

While dragging a divider, `142 × 38` (px) + a `cols × rows` caption appears
centered in the active pane (`layout/resize-overlay.js`). xterm's `fit()` is
never called every frame — the refit gate in `layout/surface.js` is what defers fit
during a drag, and the overlay must not defeat that optimization, so it
estimates the cell count per frame from the cell size measured once at drag
start. The overlay fades out 200ms after the drag ends, and under
`prefers-reduced-motion` it disappears immediately with no fade.

### What's left as legacy

`#vt-rail` (the 48px icon rail — session/queue/snippets/ports/usage/settings,
6 buttons) and `#vt-right-rail` (a right rail dedicated to usage) are **still
in the DOM but hidden via CSS** (`styles/screens/70-worktree-rail.css`'s
`#vt-rail { display:none !important; }`). The worktree rail (`#vt-wgrail`)
and the dock's usage tab each take over that role. Only
`#vt-rail-settings-tpl` (the desktop mic slot, theme-chip flyout) is
kept — Rail.tsx's "more" overflow reopens the same button inside it.

## 4. Code viewer / file pane

Where a file opens is the `viewer.surface` setting (Settings → 모양, per
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
tooltip) and a 「페인으로」 button (`columns-2` icon) that moves the file into
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
  are 유휴 / 작업 중 / **입력 대기** / 완료 (waiting reads 입력 대기 everywhere). A tab or group is represented by one state with the
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

**Three settings, three icons.** 설정 = gear (reached from the FarShell menu, `Mod+,`, with no gear button in the dock), 레일 설정 = `sliders-horizontal` (rail header), 메뉴바 설정 = `panel-top` (FarShell menu item).

Settings are split across two scopes — `frontend/js/core/settings.js`'s
`SCHEMA` gives each key a `scope: 'global' | 'device'` (default `global`).

- **global**: `/api/workspace.settings` (server) — a value changed on the
  phone shows up on the Mac browser too. Keymap, skin, autocopy, scrollback
  chunk count, etc.
- **device**: `GET/PUT /api/device-settings` — the server identifies the
  device via the `vt_device` cookie and stores it at
  `~/.vt/device-settings/<device_id>.json` (0600). Values that only make
  sense "on this device": rail/dock width and collapse state, terminal font
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
  and `security.js`'s device table (기기/id/등록/마지막 사용) do; otherwise the
  columns run off the right edge on a phone.
- **ON/OFF toggles do not stretch.** The compact media query's
  `.vt-set-row { flex-direction:column; align-items:stretch }` (label above
  control) stretches every row child to full width, which suits a `<select>`
  or a button but turns a two-cell segmented switch into a giant bar with no
  visual target. `.vt-toggle` sets `align-self:flex-start`.
- **Range slider thumbs are 20px under the compact media query** (both
  `::-webkit-slider-thumb` and `::-moz-range-thumb`). The desktop affordance
  is `:hover { transform:scale(1.15) }`, which never fires on touch, so the
  knob would stay a tiny 12px.
- **MCP server rows' Korean explanatory text uses the UI font**
  (`.vt-mcp-meta`/`.vt-mcp-verify`, `--ui`). Hangul in a monospace face has
  uneven glyph spacing and reads worse; a lone identifier inside a sentence
  doesn't need its own font to be legible.
- **MCP server row buttons (켬/끔, 검증) are 32px tall** on compact, not the
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
| `@` | `session` | Sessions — the *only* place sessions are listed in the palette |
| `:` | `keymap` | Commands — shows **the current key binding** next to each (read from the `core/keymap.js` registry, so a rebind shows up here too) |
| `#` | `queue` | Prompt queue items |
| `!` | `port` | Open ports |
| `~` | `scrollback` | Server-side scrollback search (`GET /api/search/scrollback?q=` — greps the full session ring buffer) |
| `>` | `settings` | Settings (theme, push, etc. — the home of the settings that have no other entry point) |

Two-column layout — left: result list, right: a **preview** (a file shows a
highlighted excerpt of its start, a session shows its last lines + state, a
scrollback result shows surrounding context). The preview hides on narrow
widths. `⌘F` opens the palette's `~` mode; the in-pane search bar
is on `⌘⇧F` (rebindable).

### Default screen

The no-prefix screen lists no sessions (session switching lives in the
세션 tab, and `@` is the only palette path) and the command list covers
everything: `settings.show`/`scm.show`/`clients.show` (no rail button pairs with these, so they are in
`DEFAULT_COMMANDS` **without** `isPanel: true`, which keeps
`rail-palette-parity.test.js`'s check from firing on them) plus every
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

```
┌ app bar 52 ── session name ▾                     [keybar] [search] ┐
│               MacBook Pro M1 Pro · n/N                             │
│                                                                     │
│   tab content (session / terminal / changes / files / settings)    │
│                                                                     │
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
the bar but is reachable from the command palette's `:` mode. Files (attachments)
has a slot and shows a badge
of in-flight upload jobs (`window.vtUploadJobs()`, event-driven off
`vt:upload-progress` rather than polled). Settings opens straight into
Settings, whose own first screen already lists those as "Tools". Each
full-screen panel stays inside the mobile navigation shell and Android/browser
Back closes that panel first. There's no menu bar or second status row: waiting
count is a badge on the Session tab. When the software keyboard opens, the
bottom nav hides and the keybar moves down to the keyboard edge so both bars
do not consume the same vertical space — and `#terminal-container`'s bottom
padding accounts for the 52px bottom nav in addition to the keybar, otherwise the
prompt line and tmux status line hide behind the keybar whenever the nav is showing.

**Fleet home** (`shell/Fleet.tsx`, labelled "세션" on the bottom nav) is
the first screen (`ui.mobile.home` defaults to `fleet`) — most of what you do
on a phone in compact is "what's waiting on me right now," not typing. Its
data source is the same `/api/tmux/sessions` + `allSessions()` join as the desktop rail's local path
(`Rail.tsx`), with the **same rows and zones**:
`collectSessionRows` (`rail-fetch.ts`) builds them, `buildRailLists` (`rail-data.ts`) splits
them into 개입 필요 and 세션 (name order), and the row parts (state icon + unseen ring,
keyword line, origin icon) are `RailRow.tsx`'s own components. Only the waiting rows in the
개입 필요 zone carry the inline approval buttons (the same session also appears below
without them). Unseen is shown by the ring on the state icon, not a separate badge.

**Long-press a row for a bottom-sheet menu** — the same items as the desktop ⋯ menu
(`rowMenuItems`: open rows get 닫기 · 이름 변경 · 맥에서 열기 · 연결된 화면, closed rows get
열기; tmux sessions add 종료, red), the same window bridges as the desktop rail
(`runRowMenu`), just a sheet instead of a coordinate popup — a touch menu
that opens where you pressed can walk off-screen. Detection is a plain
pointer-event timer (500ms / 8px cancel, touch-only — mouse is left to the
desktop's ⋯ button) since `layout/dnd.js`'s long-press is wired to actual
drag-and-drop and isn't reusable as-is. **Two pitfalls:** the sheet's action callbacks must capture the row into a
plain variable before calling `closeMenu()`; reading the render-prop's resolved
value (`m()`) afterwards finds the `<Show>` block already torn down, so the
action silently does nothing from a real tap while working when called from a
console. And the shared `.vt-session-sheet` (also used by the
`#voice-session-picker` sheet) pads its bottom for the safe-area **and** the
52px bottom nav, otherwise a sheet's last row/button sits behind the nav and
is untappable.

A permanent **"+ 새 세션"** button sits at the bottom of the list (hidden
only in the true empty state, where two dedicated buttons already do the
same job) and opens the same `session.add-menu` popover as the desktop rail's
own "+ 새 세션" — `Fleet.tsx` takes `getAction` as a dep (from
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
`vt-term-select-mode` class, show/hide the 복사·모두 선택·완료 bar); a long-press on the terminal
triggers it, and handle-driven `select()` calls (not a native range) run inside it. "모두 선택" selects the visible
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
variants, collapse toggle or mic slot (the mic lives only in the dock / rail slot).
Modifiers sit outside the strip so a chord is always in the same place and a thumb that
drifts while holding one does not cancel it. Modifier state reads like a
Caps Lock light: a 5px lamp top-right — faint ring (off), accent outline +
ring (next key), filled key + solid lamp (locked). Keycaps use `--mono`. The
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
skins). Two 44px icon buttons on the right: `#keybar-open-btn` (keyboard) and
search. The keybar is **closed by default** and only that button opens it —
it never opens itself when the soft keyboard appears; the
state is the device-scoped `keybar.open`. Closing also releases any latched
modifier, and text-selection mode still shows its copy/done row while
closed. Touch leaves `:hover` stuck after a tap, which would make a closed toggle
look pressed — on `(hover:none)` only `aria-pressed` paints it.

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
  returns when the keyboard goes away. The same class swaps the terminal and
  session-sheet bottom padding to the nav-less value.
- *Focus mode* — when the **visible height** (`visualViewport.height`) is under
  300px (landscape phone + keyboard: ~400px minus ~200px), `body.vt-kb-focus`
  hides the tab bar, pane headers, rail, dock, HUD and bottom nav
  (`20-overlay.css`) and releases the rail/dock body padding, so the terminal
  takes the whole remaining height. The keybar stays (it is closed by default
  anyway). No state is kept: the same function removes the class when the height
  comes back. Pointer-fine devices never get it (the keybar is not wired there).

## 8. Usage (dock "Usage" tab)

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
- If neither provider exists, the dock tab disappears entirely — `VT_USAGE_PROVIDER` (`auto`/`clauth`/`none`) controls the choice.

## 9. xterm.js terminal theme

The heart of "feels like iTerm2 vs. feels like Windows" isn't the window
chrome, it's **the terminal's own background + the 16 ANSI colors**.
`js/theme.js`'s `VT_XTERM_THEMES` defines a complete palette per skin:
`background/foreground/cursor/selection + black..white + brightBlack..brightWhite`.

- farshell: near-black (#0a0a0b) + amber cursor
- macos: deep black (#101012) + macOS system colors (red #ff453a, green #32d74b, blue #0a84ff, …)
- catppuccin: #1e1e2e + the Catppuccin Mocha palette
- windows: the **official Campbell palette** (#0c0c0c, red #c50f1f, blue #0037da, …)
- vscode: #1e1e1e + VS Code's integrated-terminal default palette
- notepad: the only light background (#fffefb) + blue cursor (#0060df)

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
  holds it: an 18px `min-width` badge inside a 36px collapsed dock tab makes the
  tab 38px wide and `overflow: hidden` cuts the rest off. Where the container is
  a fixed strip, drop the number for a 6px `--color-acc` dot positioned
  `absolute` (contributing 0px of width) and let the tooltip say the count.
- **Tooltips** (`.vt-tip`, `js/ui/tooltip.js`): one `position: fixed` element on
  `body`, driven by `data-tip` (name) and optional `data-tip-sub` (the value —
  a count, a shortcut, a reason; `--font-mono`, `tabular-nums`, `--color-sub`).
  `data-tip-side` picks `top|bottom|left|right` and flips when the side doesn't
  fit. **Never the native `title` attribute**: its ~1s delay can't be tuned, it
  can't be themed across the 9 skins, and it does nothing under touch. 400ms to
  open, instant within 300ms of the last one closing, 120ms
  opacity+scale growing from the trigger. No arrow — an arrow has to track both
  the fill and the 1px edge, and always drifts in one skin.
  A tooltip is a *description*, not a name: icon-only controls still carry
  `aria-label`, which is also the only label a touch device ever gets.
- **Toasts** (`.vt-toast` / `#vt-hud-msg`, `js/ui/toast.js`): a toast reads as a
  rail row that floated off the rail — the same 2px leading status bar (an inset
  `box-shadow`, never `border-left`, which the radius would clip), the same
  5 status colors, 12px `--font-ui`, `--font-mono` `tabular-nums` for the `×N`
  repeat badge. Color goes on the text and the bar only; filling the background
  with a status color turns three stacked toasts into a block of colour.
  **Where it lands is decided by severity, because the goal is to not cover the
  terminal.** `info`/`success` take over the 28px menu bar (`#vt-hud`) for a moment — it
  covers no terminal rows, like tmux's status line or vim's echo area (it is at the top; the toast replaces the chips in place). Only `warn`/`error` float,
  as a card at the bottom right, clear of the prompt line at the
  bottom left. In compact (<720px) there is no menu bar, so everything is a card,
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
- **Tags** (`.vt-tag`): a filled faint tint, no border, `--radius-sm`. A
  different species from `.vt-badge` — a tag doesn't count, it states a
  *property* (public exposure, trust tier, "this device", a plan name like
  `Pro`/`rolling`). The tint is derived from `currentColor`
  (`color-mix(… 16%, transparent)`), so a consumer sets only `color` and the
  ratio isn't restated at each use site. Not a pill — the whole screen is one
  5px radius step. Use this one component rather than copying the `color-mix`.
- **Tabs (header, dock)**: active = a 2px `--color-acc` bottom underline +
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
  shared mark pairs shape (eye = read, pen = write), a word (「쓰기」) and colour (`--color-st-waiting` for write only; read is
  neutral). The warning copy is plain type hierarchy (first line semibold), no box, no icon. The approval modal is a decision
  prompt: its first-focus button is the **safe** one (거부), no button is filled/accent (neither answer should look like the
  obvious one), buttons ignore input for 400 ms after drawing, and Esc is 「나중에」, never an answer. 44px under a coarse pointer.

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
  mode), `Ctrl/Cmd+Shift+F` (in-pane search), Esc to close,
  `:focus-visible` outline (`--color-acc`).
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
| `frontend/index.html` | Boot theme script (anti-FOUC), login gate, `#vt-chrome` mount point, mobile bottom nav, keybar |
| `frontend/js/layout/surface.js` | Terminal placement (which pane holds which session) · refit gate · `#vt-term-stage` waiting room |
| `frontend/js/core/types.ts` | Shared TS types |
| `frontend/js/shell/Rail.tsx` | Worktree rail, 252px |
| `frontend/js/shell/Dock.tsx` | Dock frame, 392px |
| `frontend/js/shell/Hud.tsx` · `hud-data.ts` | Menu bar, 28px at the top |
| `frontend/js/shell/HeaderExtras.tsx` | Workspace chip · connected screens |
| `frontend/js/shell/Fleet.tsx` | Mobile fleet home |
| `frontend/js/shell/Palette.tsx` · `palette-data.ts` | Command palette, 8 modes |
| `frontend/js/layout/resize-overlay.js` | px / cols×rows caption while dragging a divider |
| `frontend/js/layout/tree.js` | Pane tree — leaf `kind: 'terminal'\|'viewer'\|'panel'` |
| `frontend/js/layout/breakpoints.js` | The 4 responsive-tier boundaries (single source) |
| `frontend/js/layout/dom-ids.js` | Tree node id → DOM element id, in one place (the maker and the finder both read it) |
| `frontend/js/layout/snap.js` | Cell-multiple snapping for pane sizes — pure, unit-tested |
| `frontend/js/layout/panes.js` · `compact.js` · `dnd.js` | Pane-tree rect computation · compact mode · drop zones |
| `frontend/js/core/settings.js` | Server-backed settings store — global/device scope |
| `frontend/js/core/keymap.js` | Keymap registry |
| `frontend/js/panels/viewer/scm.js` | Dock source-control tab renderer (read-only) |
| `frontend/js/ui/icons.js` | Inline SVG icon registry (the only icon source) — includes the 5 agent marks |
| `frontend/js/ui/tooltip.js` | The one tooltip layer — `data-tip` / `data-tip-sub` / `data-tip-side`, replaces native `title` |
| `frontend/js/term/xterm-setup.js` | xterm instance creation, applies `getVtXtermTheme()` |
| `server/usage/base.py` · `clauth.py` · `counter_jsonl.py` · `ollama.py` | LimitProvider/CounterProvider |
| `server/device_settings.py` | Device-scoped settings storage |
| `frontend/js/voice/` | Recording/STT/TTS, media keys, voice-only mode — built as its **own** lib entry (`frontend/dist/voice.js`), lazy-loaded only when the voice capability is on |
