# FarShell Design System

[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./DESIGN.ko.md)

> **As of v2.1.0.** The September 2026 "new shell" overhaul (N15~N46) rewrote
> the surface layer, the Solid/TS shell, the rail, the dock, the HUD, and the
> palette — see [ARCHITECTURE.md](./ARCHITECTURE.md) §2 for the module map.
> 2.1.0 still operates **per session** — the worktree model (N8) and
> source-control writes (push/PR, N30~32) land in 2.1.1, and multi-host
> (N39) and the repo-hash color dots (N33 §5) are deferred to 2.1.1~2.1.2
> (ADR-10, ADR-20). This document describes **only what's in the code today.**

---

## 0. Design principles

These are the rules this project actually holds itself to. Read this before
drawing a new screen.

### Restraint is taste

Gradients, animations, glassmorphism, decorative icons — none of them go in
just because they *can*. Every visual element has to earn its place, and the
default is always the simpler thing.

### Color — 70/20/10

Four neutral background steps (`--color-bg-0..3`) are the 70%, three text
steps are the 20%, and the accent is the 10%.

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
- Don't mix libraries — there's one registry, `frontend/js/ui/icons.js`, inline SVG.
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
  Shadow is gray, low-opacity, and used only to state elevation on overlays
  (palette, sheets, dialogs) via `--shadow-e2`/`e3`. Every other boundary is
  a 1px line (`--color-line`) — `--shadow-e1` was removed in 2.1.0.
- The exception: a 0-blur accent ring is not a shadow, it's a **focus ring** —
  an accessibility affordance, and it stays.

### Hierarchy — flatten nesting

- No cards inside cards. If a nested card is really just the parent's
  content, drop it.
- Secondary info is small and dim (`--color-sub`/`--color-muted`), not boxed.
- Every border line must be able to answer "what does this line teach the user?"

### Motion

- No entrance animation (fade-in, slide-up) as a default on content.
- Never stack multiple transforms on hover.
- Animation is 120~200ms, subtle, and purposeful — feedback, state change, or
  spatial sense only (`--dur-fast`/`--dur-base`, `--ease-out-quint`).
- **Only one thing moves per screen.** Of the status dots, only `waiting`
  breathes — if every dot pulses on a screen full of sessions (rail, fleet
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

## 1. Skins — 6

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

Switched from the header's ⚙ settings, or the theme chip in the command
palette (`Mod+K`, shows up mixed into a bare search, or under the `>`
settings mode). A boot-time inline script in `<head>` commits `data-skin`
before first paint (anti-FOUC).

## 2. Tokens

| File | Holds |
|------|-------|
| `styles/theme/tokens.css` | Structural tokens (spacing, fonts, sizes, radius, shadow, easing, breakpoints, shell dimensions) + the default skin `farshell`'s color tokens. Declared as Tailwind v4 `@theme static`, which compiles to real `:root` custom properties |
| `styles/theme/skins.css` | The same color tokens remapped for the other 5 skins |
| `styles/layers/components.css` | `.status-dot` — the home for new components |
| `styles/layers/legacy.css` | Every existing component + `.vt-*` overlays + the rail/dock/HUD frame's CSS. Unlayered, so it always wins the cascade |

Vite builds all four into `frontend/dist/app.css`.

### Color tokens (farshell baseline)

The contract is **20 colors** (4 background + 2 divider + 3 text + 6 accent +
5 status), plus 2 terminal colors handed to xterm and 1 surface-state color
that ride alongside. In 2.1.0 the corner radius dropped to one step
(`--radius-md` removed) and the 4-step background order changed.

| Token | Value | Use |
|------|-----|------|
| `--color-bg-0` | `#08090a` | Deepest background |
| `--color-bg-1` | `#0e1011` | Chrome (header, rail, dock, HUD) |
| `--color-bg-2` | `#0b0d0e` | Row headers, subbars — **darker than `bg-1`.** A subbar has to sit one notch below the chrome itself to read as a "subheading laid on a row" (a change from 2.0's monotonically increasing order) |
| `--color-bg-3` | `#141719` | Hover / active row |
| `--color-line` / `--color-line-strong` | `rgb(255 255 255 / .07)` / `.14` | Two divider steps |
| `--color-txt` / `--color-sub` / `--color-muted` | `#e8e9ea` `#8b8f93` `#666a6e` | Three text steps (`muted` was brightened from the spec draft to clear 3:1 on `bg-3`) |
| `--color-acc` | `var(--acc-farshell)` = `#f0a860` | The accent. Kept, together with the 5 status colors, **for those two roles only** |
| `--color-acc-dim` | `#b3803f` | Badge border, outline |
| `--color-acc-surface` | `var(--color-acc)` (skin-overridable) | The surface under `acc-ink` text (primary buttons, active-tab underline background). Defaults to the accent itself — **only `macos` overrides it to `acc-dim`**, since white text on Apple's system blue measured 3.65:1 |
| `--color-acc-ink` | `#17140f` | Text on top of the accent |
| `--acc-farshell/macos/catppuccin/windows/vscode/notepad` | Each skin's own accent hex | **Out-of-scope constants.** The theme chip needs to show the accent dot of "a different skin than the one active now," so these live outside `data-skin`'s scoping alongside the scoped `--color-acc` — they're the single source both the chip dot and each skin's `--color-acc` read |
| `--color-st-{idle,working,waiting,done,error}` | `#7c7c84` `#4fd1a5` `#f0a860` `#5b8def` `#f06868` | The 5 agent states (§5) |
| `--color-term-bg` / `--color-term-fg` | `#0a0a0b` / `#eeeef0` | xterm background / foreground |
| `--color-surface-active` | `rgb(255 255 255 / .18)` | The "currently selected" surface (rail, fleet home rows). Twice the hover overlay's alpha — the same value would be indistinguishable from hover on a pointer device. The accessibility requirement (WCAG 1.4.11) is carried by the row's left 3px status bar, not by this |
| `--color-on-solid` | `#ffffff` | Fixed white text over status banners, etc. |

### Contrast rules

The bar differs per role. Forcing `muted` to 4.5:1 too would erase the
distinction from `sub` and collapse the 3-step text ramp, so this follows
what WCAG actually requires per role.

| Token | Bar | Basis |
|---|---|---|
| `txt` | **4.5:1** | Body text (WCAG 1.4.3 AA) |
| `sub` | **4.5:1** | Secondary but still-read text |
| `muted` · `st-*` | **3:1** | UI components / graphical objects (WCAG 1.4.11) |

The bar must hold across **all four background steps** — the same color has
actually passed on `bg-0` and failed on `bg-3` before.

**Measured (worst case across 6 skins × 4 background steps, computed the same
way `node --test frontend/tests/tokens-contrast.test.js` computes it):**

| Skin | `txt` | `sub` | `muted` |
|---|---|---|---|
| farshell | 14.81 | 5.53 | 3.30 |
| macos | 9.12 | 4.52 | 3.02 |
| catppuccin | 6.31 | 4.52 | 3.00 |
| windows | 9.18 | 5.24 | 3.25 |
| vscode | 8.68 | 4.59 | 3.07 |
| notepad | 11.53 | 4.78 | 4.28 |

This table is recomputed on every CI run by
`frontend/tests/tokens-contrast.test.js` — you don't need to hand-update this
document every time a token value changes, but if the table drifts far
enough, refresh it.

### Structural tokens

- **Spacing:** a single `--spacing: 4px` scale. Every margin is
  `calc(var(--spacing) * N)`.
- **Font size:** 7 steps, `--text-2xs`(10.5) through `--text-2xl`(28).
- **Radius:** `--radius-sm`(**5px**, every control) / `--radius-lg`(**10px**,
  outer frames, sheets) / `--radius-full`. **`--radius-md` was removed in
  2.1.0** — every use site was replaced with `sm`. The `windows` and `vscode`
  skins override `--radius-sm` to 2px inside the skin for their square UI.
- **Shadow:** only `--shadow-e2`/`e3` remain (overlay-only).
  **`--shadow-e1` was removed.**
- **Motion:** `--dur-fast`(120ms) / `--dur-base`(200ms), `--ease-out-quint`.
- **Breakpoints:** `--breakpoint-regular`(720px) / `--breakpoint-wide`(1280px) /
  `--breakpoint-xwide`(1600px). The JS-side constants live in exactly **one**
  place, `frontend/js/layout/breakpoints.js` (`COMPACT_MAX`/`REGULAR_MAX`/`WIDE_MAX`).
- **Shell dimensions:** header 36px · worktree rail default 252px (resizable
  240~480) · dock default 392px (320~560, collapses to 36px) · HUD 24px. The
  rail's and dock's width/collapse state are **device-scoped settings**
  (`ui.rail.*`/`ui.dock.*`, §6 "Settings") — collapsing it on a phone must not
  affect the Mac.

The values below have **the code as the single source of truth**; this
document merely copies them. A copied value eventually drifts, so
`check_design_md()` in `scripts/check_docs.py` diffs this table against the
code directly in CI (I3). Why a table rather than scanning prose: a bare
number like `7` or `720` always exists somewhere in a doc's prose, so
scanning prose lets a wrong value slip through unnoticed (verified this by
testing it).

### Code-derived values

| Key | Value | Source |
|---|---|---|
| `skins` | `farshell, macos, catppuccin, windows, vscode, notepad` | `frontend/js/theme.js` — `VT_SKINS` |
| `breakpoints` | `720/1280` | `frontend/js/layout/breakpoints.js` — `COMPACT_MAX`/`REGULAR_MAX` |
| `pane-cap` | `2/4/6` | `frontend/js/layout/dnd.js` — `tierCap()` (compact/regular/wide; xwide is `Infinity`, so it isn't captured in the table) |
| `rail-items` | `7` | `frontend/index.html` — count of `.vt-rail-btn` (**the legacy 48px icon rail `#vt-rail`, hidden via CSS in 2.1.0** — see §3. The new shell's worktree rail `#vt-wgrail` is not counted here) |
| `agent-states` | `5` | `server/agent_status.py` — `STATUSES` (`error` is reserved, §5) |
| `color-bg-0` | `#08090a` | `styles/theme/tokens.css` |
| `color-acc` | `#f0a860` | `styles/theme/tokens.css` |
| `color-txt` | `#e8e9ea` | `styles/theme/tokens.css` |

### Legacy aliases

`--win`/`--bar`/`--acc`/`--txt`/`--sub`/`--line`/`--ok`/`--err`/`--crust`/
`--trad` etc. in `legacy.css` are aliases pointing at the `--color-*` tokens
above. Bulk-replacing the hundreds of consumers with `var(--color-*)` is a
separate, much larger job of moving where the value comes from — it hasn't
been done. **New code reaches for `--color-*` directly.**

### Typography

`--font-ui`: IBM Plex Sans 400/500/600 (bundled, UI chrome only). `--font-mono`:
prefers a locally installed JetBrains Mono (terminal, kept from 2.0's ADR-9),
falling back to the bundled IBM Plex Mono 400/600 — neither path makes a
network request.

OS-mimicking skins use each OS's system font. There, `system-ui` isn't "gave
up on typography" — it's the **authentic choice for impersonating iTerm2 /
Windows Terminal**.

- macOS: `-apple-system, "SF Pro Text"` (UI), `ui-monospace, "SF Mono", Menlo` (terminal)
- windows: `"Segoe UI"` (UI), `"Cascadia Code", "Cascadia Mono", Consolas` (terminal)
- catppuccin: `system-ui` (UI), `ui-monospace, "SF Mono", Menlo, Consolas` (terminal)

## 3. Shell layout

2.0's `⋯` overflow menu is gone entirely (ADR-8, 2.0). 2.1.0 rebuilt that
skeleton on top of a surface layer (N16~N18, ADR-26) — **`#vt-chrome`**
(everything Solid owns: header, rail, dock, HUD, pane chrome) **is separate
from `#vt-surface`** (the layer that owns only position/size for xterm
wrappers), so resizing panes or switching the active one never recreates
xterm or sends an unnecessary `resize` to the server. Each `.vt-term` inside
`#vt-surface` is attached to the DOM exactly once and never moved again —
`layout/surface.js` only ever updates its `transform`. A refit (and the
`resize` sent to the server) fires only when a rect's width/height actually
changed.

```
┌ header 36 ────────────────────────────────────────────────────────────┐
│ [workspace chip] [tabs…] [+]                     [⌘K] [screens N] [⚙]│
├──────────┬───────────────────────────────────────────┬───────────────┤
│ rail 252 │ pane tree (surface layer)                  │ dock 392      │
│(worktree)│ ┌ pane header 24 ┐                         │ ┌ tabs 28 ┐   │
│          │ │ cwd · agent     │                        │ │scm|q|…  │   │
│          │ └─────────────────┘                        │ └─────────┘  │
├──────────┴───────────────────────────────────────────┴───────────────┤
│ HUD 24                                                                │
└─────────────────────────────────────────────────────────────────────┘
```

### 4 responsive tiers

| Tier | Width | Rail | Dock | Pane cap | Rendering |
|---|---|---|---|---|---|
| compact | < 720px | none (fleet home, §7) | a tab in the bottom nav | 2 | 1 pane fullscreen, swipe left/right |
| regular | 720~1279px | 48px icon rail (legacy, `#vt-rail`) | collapsed to 36px by default, opens as overlay | 4 | split tree |
| wide | 1280~1599px | 252px (`#vt-wgrail`) | collapsed to 36px by default, opens by pushing | 6 | split tree + rail |
| xwide | ≥ 1600px | 252px | 392px, open by default | **unlimited** (N4) | same as wide + cap lifted |

The rail's and dock's open/width state are **device-scoped settings**
(`ui.rail.collapsed`, `ui.rail.width`, `ui.dock.collapsed`, `ui.dock.width`) —
collapsing it on a phone must not carry over to the Mac. The tier boundaries
live in exactly one place, `layout/breakpoints.js`. Past the cap, the split
button goes disabled **with the reason in its tooltip**.

### Header 36px

Left: a workspace chip (repo name + host, clicking it opens the palette's `@`
mode). Center: tabs — **in 2.1.0 a tab is a session** (a worktree from 2.1.1,
ADR-20). A tab is `[14px agent mark][name][status dot][unread badge]`. Right:
`⌘K` (palette), a connected-screens icon + count, settings ⚙. Dragging a tab
to drop it on a pane still uses the existing `layout/dnd.js`
(`frontend/js/shell/HeaderExtras.tsx` only draws the new workspace chip and
connected-screens icon; it doesn't touch the tabs themselves).

### Worktree rail 252px (`#vt-wgrail`, `shell/Rail.tsx`)

2.1.0 has no worktree model yet (that's 2.1.1's job), so **a session is drawn
in the worktree row's place** — one session, one row. A row is: a left 3px
**status bar** (`--color-st-*` tone — idle/working/waiting/done/error, same
colors as §5) · agent mark · name · a diff badge on the right (from `GET
/api/git/status` keyed on the session's cwd, cached 60s) · a second-line
status sentence. Group order is fixed: **needs attention** (waiting, error)
→ **working** → **idle** (idle, done), most-recently-active first within a
group. Clicking a row focuses that session in the active pane. The rail's
width resizer spans 240~480px. Collapsed (48px), only the mark and the
status bar remain.

> Not yet built: the plan document (20-design-system.md §5) proposed
> "repo-name-hash → color dot" (a color-dot ramp) — that's **not in the 2.1.0
> code**. The left bar currently carries only the status color. Per-repo
> distinction is revisited alongside the worktree model in 2.1.1.

### Dock 392px (`shell/Dock.tsx`)

Fixed to the right, collapses to a 36px strip via `›` (no bottom dock — ADR-21,
to keep terminal vertical space). 4 tabs: **Source control · Queue · Ports ·
Usage**. The frame owns only tab switching, collapse, the width resizer
(320~560), and device-scoped persistence — each tab's content is drawn by the
existing panel renderers (`queue.js`, `ports.js`, `panels/usage.js`,
`panels/viewer/scm.js`) transplanted as-is. The 4 modal popup panels from 2.0
are gone; the existing action ids (`queue.show`, `ports.show`, `usage.open`,
`scm.show`) are kept and now rewired to open a dock tab instead (the palette
and keymap still reference those same ids). The ports and usage tabs
disappear entirely when `/api/capabilities` reports no source for them.

**The source-control tab stays read-only through 2.1.1** (ADR-22) —
stage/unstage/commit/push/PR creation need an elevated session (`POST
/api/auth/elevate`) and a git-account store, both of which land in 2.1.1.
2.1.0 offers status and diff viewing only.

**The code viewer isn't in the dock at all.** Picking a file in the palette's
`/` mode opens it **as a pane** (leaf generalization, N4 — `layout/tree.js`'s
leaf now carries `kind: 'terminal' | 'viewer'`. A `viewer` leaf always has
`session: null` and only a file path). 2.0's modal code-viewer backdrop is gone.

### HUD 24px (`shell/Hud.tsx`)

A row of chips like `server :7777 · tunnel named · E2E ON · safe mode ON ·
screens 3 · Claude 62% · resets in 4h12m · v2.1.0`. Polled every 30s, plus
immediate refresh on `/ws-notify` events. A chip with no value is **hidden**
(no usage provider → no chip, the 2.0 rule kept). There is no HUD at all in
compact — the bottom nav takes its place.

### Resize overlay (N43)

While dragging a divider, `142 × 38` (px) + a `cols × rows` caption appears
centered in the active pane (`layout/resize-overlay.js`). xterm's `fit()` is
never called every frame — the surface layer's refit gate is what defers fit
during a drag, and the overlay must not defeat that optimization, so it
estimates the cell count per frame from the cell size measured once at drag
start. The overlay fades out 200ms after the drag ends, and under
`prefers-reduced-motion` it disappears immediately with no fade.

### What's left as legacy (not yet removed)

`#vt-rail` (the 48px icon rail — session/queue/snippets/ports/usage/settings,
6 buttons) and `#vt-right-rail` (a right rail dedicated to usage) are **still
in the DOM but hidden via CSS** (`legacy.css`'s
`#vt-rail { display:none !important; }`). The worktree rail (`#vt-wgrail`)
and the dock's usage tab each take over that role. Only
`#vt-rail-settings-tpl` (the desktop mic slot, theme-chip flyout) was left
untouched — Rail.tsx's "more" overflow reopens the same button inside it.

## 4. Code viewer / file pane

A `layout/tree.js` leaf with `kind: 'viewer'`. Picking a file in the
palette's `/` mode opens that file as a new pane — one file at a time, no
file tree. It shares the same split/drag/resize rules as a session pane
(leaf generalization, N4). **Read-only**, with no write API
(`server/fsguard.py`'s three-layer defense is unchanged from 2.0 — a fixed
root `VT_BROWSE_ROOTS`, `Path.resolve()` + `is_relative_to`, and a filename
denylist).

## 5. Agent state — 5 values (4 observable)

The server decides one state per session; **the frontend only draws it.**

The contract is 5 values — `server/agent_status.py`'s `STATUSES = (IDLE,
WORKING, WAITING, DONE, ERROR)`. Only the first 4 actually have an entry path
— `error` is marked in the same file as *"reserved — no entry path"*. The
color token (`--color-st-error`) and the sort priority already have a spot
reserved. So "4 states" means **the states currently observable**, while
every consumer (tabs, pane header, worktree rail, fleet home, favicon, app
icon badge) must still handle all 5.

- The component contract is one thing: `.status-dot[data-state="..."]`
  (`styles/layers/components.css`). Color comes from `--color-st-*`.
- **Only `waiting` breathes** — the most valuable state is neither running
  nor done, it's **waiting on your input**.
- The `working` indicator on a card/tab/rail row animates only a **single
  small dot**, never the whole element (`opacity`/`transform` are
  compositor-only, so there's no repaint).
- Without the Claude Code hooks (`fsh hooks install`), this value never
  arrives at all — `fsh doctor` and Settings → About surface the gap.

### 5 agent marks (N33)

Registered in `frontend/js/ui/icons.js`: a 14px square, stroke 1.75,
`currentColor`.

| Key | Shape | For |
|---|---|---|
| `agent-claude` | Spark (4-point star) | claude |
| `agent-codex` | Bracket `[ ]` | codex |
| `agent-local` | Chip (square + 4 pins) | local LLMs (ollama, lmstudio, qwen, etc.) |
| `agent-ssh` | Globe (circle + meridians) | remote SSH sessions (2.1.2, N39) |
| `agent-shell` | Prompt `>_` | plain shell (no agent), unknown names, gemini/aider (2.1.0 only — whether they get a dedicated mark is revisited in 2.1.1) |

## 6. Settings · palette

### Settings (`Mod+,` / header ⚙)

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
  grow the Mac terminal's font (N3).

Key bindings are rebindable, and **`passthrough` hands a key back to the
terminal** — that's how you get `Mod+F` back as the shell's `forward-char`.
Turning off "forward mouse events to the app" in the mouse section makes
drag-select always work even under vim/tmux mouse mode.

### Command palette (`Mod+K`, `shell/Palette.tsx`)

Merges 2.0's `quickopen.js` (3 prefixes: `/` file, `:` command, and no `>`)
and the in-pane `search.js` into one (N5/N40/N46). 8 prefixes:

| Prefix | Mode | Source |
|---|---|---|
| (none) | `default` | Sessions, files, and commands mixed into the top results |
| `/` | `file` | File fuzzy search (`GET /api/fs/search?q=`, debounced) |
| `@` | `session` | Sessions (worktrees from 2.1.1) |
| `:` | `keymap` | Commands — shows **the current key binding** next to each (read from the `core/keymap.js` registry, so a rebind shows up here too) |
| `#` | `queue` | Prompt queue items |
| `!` | `port` | Open ports |
| `~` | `scrollback` | Server-side scrollback search (`GET /api/search/scrollback?q=`, N40 — greps the full session ring buffer) |
| `>` | `settings` | Settings (theme, push, etc. — this replaces 2.0's `⋯` "Settings" group, so removing it would be a pure regression; added as an 8th prefix on top of the plan document's 7 modes — an undocumented decision, noted here) |

Two-column layout — left: result list, right: a **preview** (a file shows a
highlighted excerpt of its start, a session shows its last lines + state, a
scrollback result shows surrounding context). The preview hides on narrow
widths. `⌘F` is rewired to the palette's `~` mode; the old in-pane search bar
is demoted to `⌘⇧F` (rebindable).

## 7. Mobile (compact, < 720px)

```
┌ top 24 ── [host chip]                             [waiting N] ┐
│                                                                 │
│   tab content (fleet / terminal / changes / queue / settings)  │
│                                                                 │
├ keybar (terminal tab only) ────────────────────────────────────┤
├ bottom nav 52 ── fleet | terminal | changes | queue | settings ┤
└──────────────────────────────────────────────────────────────┘
```

Bottom nav has **5 tabs** (`frontend/index.html`'s `#vt-mnav-bottom`). The
dock's 4 tabs (source control, queue, ports, usage) get promoted here into
"Changes" (= source control) and "Queue"; ports, usage, and files live under
"Settings" instead. There's no HUD; the 24px top bar shows only a host chip
and the waiting count.

**Fleet home** (`shell/Fleet.tsx`, ADR-27) is the first screen
(`ui.mobile.home` defaults to `fleet`) — most of what you do on a phone in
compact is "what's waiting on me right now," not typing. It reuses **exactly
the same** data source, grouping, and sort as the worktree rail
(`rail-data.ts`), so "top of the desktop rail" is always "top of the phone"
too. When a `waiting` row's detect pattern captured numbered choices, an
**inline approval button** appears (`1 · Yes` / `3 · No`) — otherwise just a
"Go to terminal" button.

## 8. Usage (dock "Usage" tab)

Two interfaces in `server/usage/base.py` (N10/N41):

```python
class LimitProvider:    # limit-based — remaining fraction
    def read(self) -> list[LimitGauge]     # {label, pct, resetsAt, window}
class CounterProvider:  # counter-based — cumulative, no limit
    def read(self, since) -> list[Counter] # {label, tokens, seconds, tokPerSec, samples}
```

- **Limit-based**: `usage/clauth.py` — reads clauth's `~/.clauth/status.json`
  (same source as 2.0). Only limit-based gauges appear on the HUD.
- **Counter-based**: `usage/counter_jsonl.py` — reads
  `~/.vt/usage-counter.jsonl`. Populated by `fsh usage add --model ...
  --tokens ... --seconds ...` (CLI) or `POST /api/usage/counter` — which
  script actually makes that call is up to the user; FarShell only stores
  and displays. `usage/ollama.py` (optional) surfaces the running local
  model as a counter label whenever `localhost:11434/api/ps` responds.
- If neither provider exists, the dock tab disappears entirely (2.0 rule
  kept) — `VT_USAGE_PROVIDER` (`auto`/`clauth`/`none`) controls the choice.

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
- **Badges**: 1px `--color-line` border, no fill, `--radius-sm`. Only status
  badges get a status-colored border.
- **Tabs (header, dock)**: active = a 2px `--color-acc` bottom underline +
  `--color-acc-surface` background. Inactive = `--color-sub` text.
- **Buttons**: default = outline only. The primary action (commit, issue
  link, create-and-switch) = filled `--color-acc` + `--color-on-solid` text.
  **One filled button per screen.**
- **Toggles**: a segment that reads as **text** first (`[ON][OFF]`), never a
  switch. The active segment gets an accent border.
- **Inputs**: `--color-bg-0` background, `--color-line` border,
  `--color-line-strong` on focus. No glow.
- **Dividers**: 1px line, `line-strong` on hover, accent while dragging.

## 11. Accessibility

- **Touch targets:** under a coarse pointer, the bottom nav and keybar
  buttons keep a 44px tap area. A fine pointer keeps a tighter density.
- **Keyboard:** `Mod+K` palette, `Mod+,` settings, `Ctrl/Cmd+F` (palette `~`
  mode), `Ctrl/Cmd+Shift+F` (in-pane search), Esc to close,
  `:focus-visible` outline (`--color-acc`).
- **Screen readers:** icon buttons carry `aria-label`; mic status is
  `role="status" aria-live="polite"`.
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
| `styles/theme/skins.css` | Color remapping for the other 5 skins |
| `styles/layers/components.css` | `.status-dot` — the home for new components |
| `styles/layers/legacy.css` | Every existing component + `.vt-*` overlays + the rail/dock/HUD frame's CSS (unlayered) |
| `frontend/index.html` | Boot theme script (anti-FOUC), login gate, `#vt-chrome`/`#vt-surface` mount points, mobile bottom nav, keybar |
| `frontend/js/layout/surface.js` | Surface layer placement · refit gate (N16) |
| `frontend/js/core/signals.ts` | Bridge from `core/store.js`/`layout/store.js` to Solid signals |
| `frontend/js/core/types.ts` | Shared TS types |
| `frontend/js/shell/Rail.tsx` | Worktree rail, 252px (N36) |
| `frontend/js/shell/Dock.tsx` | Dock frame, 392px (N35) |
| `frontend/js/shell/Hud.tsx` · `hud-data.ts` | HUD, 24px (N34) |
| `frontend/js/shell/HeaderExtras.tsx` | Workspace chip · connected screens (N37) |
| `frontend/js/shell/Fleet.tsx` | Mobile fleet home (N38) |
| `frontend/js/shell/Palette.tsx` · `palette-data.ts` | Command palette, 7+1 modes (N5/N40/N46) |
| `frontend/js/layout/resize-overlay.js` | px / cols×rows caption while dragging a divider (N43) |
| `frontend/js/layout/tree.js` | Pane tree — leaf `kind: 'terminal'\|'viewer'` (N4) |
| `frontend/js/layout/breakpoints.js` | The 4 responsive-tier boundaries (single source) |
| `frontend/js/layout/panes.js` · `compact.js` · `dnd.js` | Pane-tree rect computation · compact mode · drop zones |
| `frontend/js/core/settings.js` | Server-backed settings store — global/device scope (N3) |
| `frontend/js/core/keymap.js` | Keymap registry |
| `frontend/js/panels/viewer/scm.js` | Dock source-control tab renderer (read-only) |
| `frontend/js/ui/icons.js` | Inline SVG icon registry (the only icon source) — includes the 5 agent marks |
| `frontend/js/term/xterm-setup.js` | xterm instance creation, applies `getVtXtermTheme()` |
| `server/usage/base.py` · `clauth.py` · `counter_jsonl.py` · `ollama.py` | LimitProvider/CounterProvider (N10/N41) |
| `server/device_settings.py` | Device-scoped settings storage (N3) |
| `frontend/js/voice/` | Recording/STT/TTS, media keys, voice-only mode — built as its **own** lib entry (`frontend/dist/voice.js`), lazy-loaded only when the voice capability is on |
