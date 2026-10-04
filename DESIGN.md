# FarShell Design System


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

  `--shadow-e1` was removed in 2.1.0 and **brought back in 2.1.6**. The reason
  is worth keeping: it was still referenced by `.vt-wgrail-row.active`, but
  defined only inside the `notepad` skin — so in the other five skins that
  `box-shadow` declaration was invalid and dropped, leaving the active row
  with a background step alone (1.06:1 against the rail in `farshell` —
  effectively the same colour). "Which row am I on" was carried by the accent
  text colour and nothing else. e1 is deliberately much weaker than e2: e1
  says "a row is lifted inside a list", e2 says "a panel floats over the
  screen".
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

2026-09-24 addition (2.1 D6) — the user picked these three directly from a
comparison mockup (three real products in FarShell's own category: a
terminal app and two dev-tool dashboards) rather than a from-scratch design
pass; DESIGN.md's "restraint" language in § 0 was never something the user
asked for (see the project's own decision log) — these three intentionally
depart from it, which is the point. Every value (backgrounds, the 4-step
text ramp, the accent, status colors) was solved algorithmically against
`scripts/check_contrast.py`'s thresholds rather than picked by eye — same
process the original 6 skins used, just run in the other direction (values
first, contrast computed, adjusted until passing, rather than values
inherited from an existing UI and reverse-checked).

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
| `--color-bg-3` | `#141719` | Hover / active row — also the raised surface an active rail row sits on |
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
- **Shadow:** `--shadow-e1` (the current row) + `--shadow-e2`/`e3` (overlays).
  e1 was removed in 2.1.0 and restored in 2.1.6 — see §0 for why.
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
| `skins` | `farshell, macos, catppuccin, windows, vscode, notepad, warp, linear, vercel` | `frontend/js/theme.js` — `VT_SKINS` |
| `breakpoints` | `720/1280` | `frontend/js/layout/breakpoints.js` — `COMPACT_MAX`/`REGULAR_MAX` |
| `pane-cap` | `2/4/6` | `frontend/js/layout/dnd.js` — `tierCap()` (compact/regular/wide; xwide is `Infinity`, so it isn't captured in the table) |
| `rail-items` | `8` | `frontend/index.html` — count of `.vt-rail-btn` (**the legacy 48px icon rail `#vt-rail`, hidden via CSS in 2.1.0** — see §3. The new shell's worktree rail `#vt-wgrail` is not counted here). `7`→`8` when `dir` came back alongside `files` (2026-09-22, N35 reversal) |
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
skeleton on a surface layer (N16~N18, ADR-26): `#vt-chrome` owned the pane
chrome while a separate absolutely-positioned `#vt-surface` owned every xterm
wrapper, and the two were joined by coordinates only — `layout/surface.js`
read each pane body's rect and wrote it back as a `transform`.

**2026-09-18 — the surface layer is gone. A terminal now lives inside the
pane that shows it** (`.vt-pane > .vt-pane-body > .vt-term-wrap`). The plan
doc listed four measured problems behind N16; three were about excessive
refits and are solved by things that survive (`panes.js` reuses split/pane
elements by id, and the refit gate still fires only when a rect's width or
height actually changed). The fourth — "moving an xterm wrapper in the DOM is
dangerous" — was measured and did not hold: re-parenting a live **WebGL2**
terminal left `isContextLost()` false, raised no `webglcontextlost`, kept the
1128×828 buffer, and kept drawing.

Keeping the two apart had a real cost, because the terminal was not a
descendant of its pane: `overflow: hidden` and `border-radius` on the pane
could not clip it, an accent *surface* could not be used for the active pane
(the terminal painted over it, leaving a single 1px outline as the only
signal), and xterm's cell quantization remainder (measured 22×8px) was
stranded on one side with no way for the pane to treat it as padding. What
is left of `layout/surface.js` is the refit gate — which never needed
coordinates — plus `#vt-term-stage`, a hidden **waiting room** holding the
wrappers of sessions that are not currently placed in any pane (they must
stay in the DOM, and `visibility: hidden` keeps their box measurable so
cols/rows survive).

```
┌ header 36 ────────────────────────────────────────────────────────────┐
│ [workspace chip] [tabs…] [+]                     [⌘K] [screens N] [⚙]│
├──────────┬───────────────────────────────────────────┬───────────────┤
│ rail 252 │ pane tree                                  │ dock 392      │
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
| regular | 720~1279px | `#vt-wgrail`, device-scoped width | collapsed to 36px by default, opens as overlay | 4 | split tree |
| wide | 1280~1599px | `#vt-wgrail`, device-scoped width | collapsed to 36px by default, opens by pushing | 6 | split tree + rail |
| xwide | ≥ 1600px | same | 392px, open by default | **unlimited** (N4) | same as wide + cap lifted |

> **The rail is never the legacy icon rail.** Earlier drafts of this table had
> regular fall back to the 48px `#vt-rail`; that never shipped — `#vt-rail` is
> `display:none` at every width (see "What's left as legacy"). The worktree rail
> is the only rail, at every tier.

The tier touches the rail in exactly two places, and **neither overwrites what
you chose** — `ui.rail.width`/`ui.rail.collapsed` are device-scoped settings
(§6), and a stored value always wins.

- **Collapse default** (`defaultRailCollapsed`): on a device that has never
  stated a preference, the rail starts collapsed below wide (<1280). `Dock.tsx`
  already made this call (`innerWidth < WIDE_MAX`) and the rail didn't, so a
  narrow screen opened with both panels expanded and left the HUD 472px for
  667px of chips.
- **Rendered width clamp**: `--vt-wgrail-w` is `min(<stored>px, 40vw)`. The
  stored value says how wide you want it; the clamp asks whether the window can
  spare that. Shrink the window and a 480px rail draws at 40vw; widen it and it
  returns to 480 — the setting is never rewritten, so a wide rail survives a
  temporary narrow window. 40vw can't drop below the rail's own 240px minimum,
  because under 720px there is no rail at all (compact).

Because the variable now holds a `min()` expression rather than a plain px
number, the resizer reads the element's measured width instead of parsing it —
a custom property doesn't resolve to computed px, so `parseInt` returned `NaN`
and the rail jumped on drag.

The rail's and dock's open/width state are **device-scoped settings**
(`ui.rail.collapsed`, `ui.rail.width`, `ui.dock.collapsed`, `ui.dock.width`) —
collapsing it on a phone must not carry over to the Mac. The tier boundaries
live in exactly one place, `layout/breakpoints.js`. Past the cap, the split
button goes disabled **with the reason in its tooltip**.

### Header 36px

Left: a workspace chip (repo name + host, clicking it opens the palette's `@`
mode). Center: **view tabs** (`#vt-wtabs`, `layout/tabbar.js`). A tab is a view,
not a session, and holds only `[label][×]` — no agent mark, no state icon, no dot
(2026-10-04 rework). The label is the member names joined with `+`, or the name the
user gave it. State lives in a **state bar** along the tab's bottom edge
(`.vt-wtab-bar` > `.vt-seg`): one equal-width segment per session in pane order
(top-left first), a one-segment bar for a single view, empty panes not counted.
Segment colour = the four state colours (`--color-st-*`), idle a dimmed grey that is
always visible; only `waiting` breathes (`vt-status-breathe`; under
`prefers-reduced-motion` it becomes a striped fill, so the shape still says
"waiting"). **Thickness carries "unseen"**: 6px = changed and not yet looked at,
3px = seen. The focused pane is never shown by thickness (it would collide) — the
pane's own border does that. Colour alone is not the signal: the hover tooltip lists
`name · state[ · 확인 안 함]` per session and the tab's `aria-label` repeats it
(`api, 작업 중 · web, 입력 대기(확인 안 함)`). The active tab is a filled background
(the old accent underline gave way to the bar). × is shown even when there is one
view. Hidden below 720px, as before. Right:
`⌘K` (palette), a connected-screens icon + count, settings ⚙. Dragging a tab
to drop it on a pane still uses the existing `layout/dnd.js`
(`frontend/js/shell/HeaderExtras.tsx` only draws the new workspace chip and
connected-screens icon; it doesn't touch the tabs themselves).

### Worktree rail 252px (`#vt-wgrail`, `shell/Rail.tsx`)

2.1.0 has no worktree model yet (that's 2.1.1's job), so **a session is drawn
in the worktree row's place** — one session, one row. A row is: a left 3px
**status bar** (`--color-st-*` tone — idle/working/waiting/done, same
colors as §5) · agent mark · name · a diff badge on the right (from `GET
/api/git/status` keyed on the session's cwd, cached 60s) · a second-line
status sentence. Group order is fixed: **needs attention** (waiting)
→ **working** → **idle** (idle, done), most-recently-active first within a
group. Clicking a row focuses that session in the active pane. The rail's
width resizer spans 240~480px. Collapsed (48px), only the mark and the
status bar remain.

**Repo color bar** (`.vt-wgrail-hash`, 20-design-system.md §5 O2): a worktree
row carries a *second* 3px bar, left of the status bar, whose color comes from
`fnv1a(repo name + remote) % 8` → `--color-hash-1..8` (defined per skin, all
6). The status bar answers "does this need me"; this one answers "which repo
is this" — two questions that a single bar was collapsing. Session rows
(`kind-session`) leave it transparent, since a bare session has no repo to
hash. Implemented in `shell/RailRow.tsx` + `rail-data.ts`
(`repoColorKey`/`hashRepoColorIndex`).

### Dock 392px (`shell/Dock.tsx`)

Fixed to the right, collapses to a 36px strip via a chevron (no bottom dock —
ADR-21, to keep terminal vertical space). 5 tabs: **Source control · Queue ·
Files · Ports · Usage**.

**Collapsed, a tab is an icon — never rotated text.** The 36px strip used to
rotate each label with `writing-mode: vertical-rl`, which works for Latin
(glyphs lie on their side) but not for CJK: Korean syllables *stack*, so
「소스컨트롤」 became a five-character tower, in a monospace face. Each tab now
carries an icon from `js/ui/icons.js` (`git-branch · list · folder · plug ·
gauge`), shown only while collapsed; the full label stays in `data-tip`. The
expanded strip is text-only on purpose — five tabs in a row with icons as well
is crowded. The frame owns only tab switching, collapse, the width resizer
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
leaf now carries `kind: 'terminal' | 'viewer' | 'panel'`. A `viewer` leaf has
only a file path; a `panel` leaf has one registered dock panel name. Both keep
`session: null`). 2.0's modal code-viewer backdrop is gone.

### HUD 24px (`shell/Hud.tsx`)

A row of chips like `server :7777 · tunnel named · E2E ON · safe mode ON ·
screens 3 · Claude 62% · resets in 4h12m · v2.1.0`. Polled every 30s, plus
immediate refresh on `/ws-notify` events. A chip with no value is **hidden**
(no usage provider → no chip, the 2.0 rule kept). There is no HUD at all in
compact — the bottom nav takes its place.

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

Where a file opens is the `viewer.surface` setting (Settings → 모양, per
device; ADR-44, 2.3):

- **`sheet`** (default) — a card pinned to the top of the terminal area,
  full width, height dragged from a grip at its bottom edge (20–85%).
- **`float`** — a movable, resizable card; header drags it, the corner grip
  resizes it, and it can never be dragged somewhere it can't be grabbed back.
- **`pane`** — the 2.2 behavior: a `layout/tree.js` leaf with
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
the **frontend draws 4**: `error` is gone from the UI (no colour, branch, sentence or
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
  are 유휴 / 작업 중 / **입력 대기** / 완료 (waiting reads 입력 대기 everywhere — the
  rail's old 승인 대기 is gone). A tab or group is represented by one state with the
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
  colour. The rail rows adopt them in step 5; surfaces still draw `.status-dot` today.
- Without the Claude Code hooks (`fsh hooks install`), this value never
  arrives at all — `fsh doctor` and Settings → About surface the gap.

### 5 agent marks (N33)

Registered in `frontend/js/ui/icons.js`: a 14px square, `currentColor`, **monochrome
always** (no brand colours — 2026-09-29 decision).

**Brand logos first (2026-09-29).** Where a real logo exists it wins over the drawn
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
| `agent-aider` | Two overlapping circles (pair programming) | aider (2026-09-22) |
| `agent-agy` | Two overlapping diamonds ("twins") | Antigravity CLI, `agy` (2026-09-23 — Google folded its previous CLI into Antigravity on 2026-06-18; the mark kept its shape, same product lineage) |
| `agent-qwen` | Two triangles apex-to-apex (bowtie) | qwen-code CLI, `qwen` (2026-09-23 — independent coding-agent CLI, not lumped into `agent-local`) |
| `agent-local` | Chip (square + 4 pins) | local LLM runners detected via raw foreground command: `ollama`, `lms` (LM Studio), `llama-cli` (llama.cpp) — all three share this one mark, labels differ (2026-09-23) |
| `agent-ssh` | Globe (circle + meridians) | remote SSH sessions (2.1.2, N39) |
| `agent-python` | Two joined coils | plain `python`/`python3`/`Python` foreground process, not an AI CLI (2026-09-22) |
| `agent-node` | Hexagon | plain `node` foreground process (2026-09-22) |
| `agent-docker` | Stacked squares | plain `docker` foreground process (2026-09-22) |
| `agent-shell` | Prompt `>_` | plain shell (no agent), unknown names |

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

**Mobile bug pass (2026-09-24, 2.1 D6, step 6a — narrow fixes, not a visual
redesign).** Five compact-viewport defects, each confirmed live before/after
in an isolated server + real mobile viewport:

- **Registered-device table overflowed the screen.** `agents.js`'s coverage
  table wraps itself in `.vt-set-covtable-host` (`overflow-x:auto`);
  `security.js`'s device table never got the same wrapper, so on a phone the
  four columns (기기/id/등록/마지막 사용) just ran off the right edge instead
  of scrolling. Same fix, same wrapper class.
- **ON/OFF toggles stretched to the full row width.** The compact media
  query's `.vt-set-row { flex-direction:column; align-items:stretch }` (for
  putting label above control on narrow screens) stretches every row child
  to full width by default — fine for a `<select>` or a button, wrong for a
  two-cell segmented switch, which ends up as a giant bar with no visual
  target. `align-self:flex-start` on `.vt-toggle` only.
- **Range slider thumb stayed 12px on touch.** The desktop affordance is
  `:hover { transform:scale(1.15) }`, which never fires on a touch device —
  so the knob a mouse user sees grow on approach is permanently tiny on a
  phone. Enlarged to 20px under the same compact media query (both
  `::-webkit-slider-thumb` and `::-moz-range-thumb`).
- **MCP server rows' Korean explanatory text rendered in the monospace
  font** (`.vt-mcp-meta`/`.vt-mcp-verify` — "OAuth 서버는 다른 스코프로 복제할
  수 없습니다", "확인 불가 — 이 서버 환경에 없음: …", etc.). These classes were
  built for a mix of prose and the occasional embedded identifier (a masked
  secret, a version number), but ended up mono for everything — Hangul in a
  monospace face has uneven glyph spacing and reads worse than in the UI
  font. Switched both to `--ui`; a lone identifier inside a sentence doesn't
  need its own font to be legible.
- **MCP server row buttons (켬/끔, 검증) were `.vt-chip.sm` — 20px tall**,
  sized for desktop list density (12 servers × several chips a row). 44px
  would overlap the adjacent tag chips (only a 12px gap between them, same
  crowding tradeoff already documented for the terminal-selection handles in
  § 7) — 32px is the compromise, applied only to the two primary-action
  chips, not the tag chips that are meant to stay small and packed.

### Command palette (`Mod+K`, `shell/Palette.tsx`)

Merges 2.0's `quickopen.js` (3 prefixes: `/` file, `:` command, and no `>`)
and the in-pane `search.js` into one (N5/N40/N46). 8 prefixes:

| Prefix | Mode | Source |
|---|---|---|
| (none) | `default` | Recent commands (2) → recent files (2) → every command, grouped and scrollable — **no sessions** (see below) |
| `/` | `file` | File fuzzy search (`GET /api/fs/search?q=`, debounced) |
| `@` | `session` | Sessions (worktrees from 2.1.1) — this is now the *only* place the default screen's sessions moved to |
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

### Default screen redesign (2026-09-23, 2.1 D6)

Sessions moved out of the no-prefix screen entirely (session switching lives
in the 세션 tab now, `@` is the only palette path left) and the command list
grew to include everything: `settings.show`/`scm.show`/`clients.show` (found
missing during the audit — no rail button pairs with these, so they're added
to `DEFAULT_COMMANDS` **without** `isPanel: true`, which keeps
`rail-palette-parity.test.js`'s ADR-8 check from firing on them) plus every
non-`unavailable` `core/keymap.js` action, so the palette is a real "search
every command" surface and not just files+a fixed list.

Order on the empty-query screen: 2 most-recently-used commands → 2 most-
recent files → everything else, grouped by kind (existing
`sectionHeadAt`/`KIND_META` machinery, no new grouping code needed). Recency
is tracked by **action id** (`Row.key`, e.g. `cmd:settings.show`), not label —
a `__toggle:` command's label never changes but its target does, so id is the
only stable identity — in the new device-scoped setting
`palette.recentCommands` (JSON array, newest first, capped at 10, read/written
through `window.vtSettingsGet/Set` since `shell/Palette.tsx` can't statically
import `core/settings.js`, ADR-26). Typing a query drops the recent/files
split and just flattens everything that matches — recency ordering only
matters when you're browsing, not searching.

**Two dead keymap shortcuts, found and fixed in the same pass**: `viewer`
(⌃⇧E) and `paste` (⌃⇧V) were defined in `core/keymap.js`'s `ACTIONS` table
with labels and default bindings, but **nobody had ever called
`register()` for them** — pressing either did nothing, silently, since
`core/keymap.js`'s dispatcher just no-ops when `_handlers.get(id)` is
`undefined`. `viewer` now delegates to the existing `viewer.show` action
(`panels/viewer-lazy.js`). `paste` is registered in `term/keymap-actions.js`:
native paste shortcuts use the browser `paste` event when available, then a
delayed Clipboard API fallback; palette invocation and other rebound shortcuts
use `pasteFromClipboard(activeSessionId())` directly.

**Scroll-clipping bug, root-caused live (not by reading CSS)**: the reported
"10개인데 3개만 보인다" wasn't a missing scrollbar — nothing was clipping
correctly *or* scrolling. The palette's results container carries the class
`vt-qo-body`, which only *looks* like the code viewer's `vt-vw-body` — they
don't share a rule. `vt-vw-body{flex:1 1 auto; overflow:auto}` never applied
to it, so the div had no `flex-grow` (stayed at content size inside the
column-flex card) and no `overflow:auto` (nothing to scroll internally) —
confirmed via `getBoundingClientRect()`/`getComputedStyle()` in a real
browser: `overflow` measured `visible`, `scrollHeight === clientHeight`. The
outer `.vt-viewer-card` clips at `max-height` with `overflow:hidden`, so
anything past that height was just gone, unreachable — a hard cut, not a
disabled scrollbar. Fixed with `.vt-qo-body{ flex:1 1 auto; min-height:0;
overflow:auto; }` in `styles/screens/30-viewer.css`.

**Mobile is now a full-height bottom sheet** (`@media (max-width:719.98px)`,
mirroring the existing file-overlay `vt-file-compact` pattern) instead of the
60vh-capped card — the scroll region needs real height to be usable with a
thumb, and 60vh was tuned for a handful of file-search rows, not a "browse
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

**2026-09-23 rework (this repo's mobile-shell-revamp branch, steps 1–4 of a
larger pass).** The header no longer changes per tab — earlier it showed the
workspace chip on the Fleet tab and the session picker + search elsewhere,
which meant the same screen real estate meant different things depending on
which bottom-nav tab you were on. Now it's three things, always: an **env
chip** (`#vt-env-chip`, `layout/env-chip.js`) reading `machine` (falling back
to `hostname`) off `/api/capabilities` (`"MacBook Pro M1 Pro"` — the OS prefix
was dropped 2026-09-29 at the user's request; machine is macOS-only,
read once via `system_profiler` and cached for the process lifetime, same
pattern as the `hostname`/`version` fields), the session picker
(`#voice-session-picker`, kept for now as a display + the same rename/sleep/
kill sheet it always opened — the session tab below now has full parity with
it, so a later pass can drop the sheet trigger and make this pure display),
and the command-palette search icon. The workspace chip (repo name + host) is
desktop-only now; a phone doesn't need to see the repo name on every screen.
`#vt-wtabs`(the desktop tab strip) needed `!important` to actually stay
hidden here — `80-dock.css`'s unconditional `display:flex` (added for the
strip's horizontal-scroll behavior, loads later in the same `legacy` layer)
otherwise won whenever 2+ tabs were open; a single-tab test during step 1
missed this because `tabbar.js` hid a lone tab on its own (it no longer does —
2026-09-29 — so a single view draws its tab bar like any other).

Bottom nav has **5 tabs** (`frontend/index.html`'s `#vt-mnav-bottom`):
**Session · Terminal · Changes · Files · Settings**. Queue was dropped from
the bar — usage was low enough that it wasn't worth a permanent slot — but
the feature itself is untouched and still reachable from the command
palette's `:` mode. Files (attachments) took the freed slot and shows a badge
of in-flight upload jobs (`window.vtUploadJobs()`, event-driven off
`vt:upload-progress` rather than polled). Settings replaces the old "More"
home (which was just shortcuts to Files/Ports/Usage) — it opens straight into
Settings now, whose own first screen already lists those as "Tools". Each
full-screen panel stays inside the mobile navigation shell and Android/browser
Back closes that panel first. There's no HUD or second status row: waiting
count is a badge on the Session tab. When the software keyboard opens, the
bottom nav hides and the keybar moves down to the keyboard edge so both bars
do not consume the same vertical space — and `#terminal-container`'s bottom
padding accounts for the 52px bottom nav in addition to the keybar (a gap
that, before this pass, left the prompt line and tmux status line hidden
behind the keybar whenever the nav was showing).

**Fleet home** (`shell/Fleet.tsx`, ADR-27, renamed "세션" on the bottom nav) is
the first screen (`ui.mobile.home` defaults to `fleet`) — most of what you do
on a phone in compact is "what's waiting on me right now," not typing. Its
data source moved from "this browser's open web sessions" to the same
`/api/tmux/sessions` + `allSessions()` join as the desktop rail's local path
(`Rail.tsx`) — so **sleeping tmux sessions show up here too now**, in their
own dimmed "잠자는 중" section at the bottom (`buildSleepingEntries`), same
name and same behavior as the rail: tap wakes and opens, no ack/unseen
handling (that's an awake-only concept). The three urgency buckets above it
(개입 필요 / 작업 중 / 열려 있음) are still `buildRailSections`, now fed only the
awake rows — a sleeping-but-waiting session doesn't jump into 개입 필요, same
rule as the desktop's `buildSessionSections`. `GROUP_LABEL.idle` itself was
renamed from "열려 있지 않음" to **"열려 있음"** (`rail-data.ts`) — that bucket
has only ever meant "session open, agent idle" since ADR-29, never "no
session," so the old label was backwards; this also fixed the same wording on
the desktop rail's remote-host view, which shares the constant.

**Long-press a row for a bottom-sheet menu** — awake rows get 이름 변경 · 재우기
· 맥에서 열기 · 연결된 화면 · 완전 종료(tmux only, red); sleeping rows get 깨우기 ·
완전 종료. Same actions, same window bridges as the desktop rail's ⋯ menu
(`ctxMenuItems`), just a sheet instead of a coordinate popup — a touch menu
that opens where you pressed can walk off-screen. Detection is a plain
pointer-event timer (500ms / 8px cancel, touch-only — mouse is left to the
desktop's ⋯ button) since `layout/dnd.js`'s long-press is wired to actual
drag-and-drop and isn't reusable as-is. **Two real bugs surfaced building
this, both fixed:** the sheet's action callbacks originally read the
render-prop's resolved value (`m()`) *after* `closeMenu()` had already torn
down that `<Show>` block, so e.g. 재우기 silently did nothing from a real tap
while working fine when called directly from a console — fixed by capturing
the row into a plain variable before closing. And the shared
`.vt-session-sheet` (also used by the older `#voice-session-picker` sheet)
padded its bottom for the safe-area only, not the 52px bottom nav, so a
sheet's last row/button could sit behind the nav and be untappable — same
fix as `#terminal-container` in step 1, applied once to the shared class so
both sheets got it.

A permanent **"+ 새 세션"** button sits at the bottom of the list (hidden
only in the true empty state, where two dedicated buttons already do the
same job) and opens the same `session.add-menu` popover as the desktop rail's
own "+ 새 세션" — `Fleet.tsx` now takes `getAction` as a dep (from
`core/dom.js`, passed in by `mobile-nav.js`) for this, the same way
`Rail.tsx`/`Hud.tsx`/`Dock.tsx` already do.

**Text selection (`term/touch-select.js`, 2026-09-23) does not use the
browser's native selection at all anymore.** The step-1 investigation found
the actual defect: xterm 5.5's accessibility layer redraws every visible row
on each PTY render (a spinner, a tmux status-line clock — anything), and that
redraw replaces the row's text nodes, which collapses whatever DOM `Range`
was sitting on them. A drag-selected range surviving long enough to copy was
close to accidental. The fix isn't a patch on that mechanism — it stops using
it: long-pressing the terminal now selects the word under your finger through
**xterm's own selection model** (`term.select()`/`getSelection()`/
`clearSelection()`, the same API a desktop mouse-drag already used), which a
separate renderer paints and which the accessibility layer's churn never
touches. Two self-drawn handles (teardrops, `.vt-term-select-handles`) sit at
the range's ends; dragging either recomputes a flat `row*cols+col` index pair
and calls `select()` again — no live DOM `Range` is ever created, so there's
nothing for a redraw to collapse. Verified directly (not just reasoned about):
selecting text, then having the pane print five lines over five seconds
(forcing the accessibility redraw repeatedly), left `term.getSelection()`
byte-for-byte unchanged throughout.

`keybar.js`'s `setTerminalSelectionMode` still owns entry/exit (blur the
hidden textarea, hide the software keyboard, toggle the
`vt-term-select-mode` class, show/hide the 복사·모두 선택·완료 bar) — this
file only changed *what triggers it* (a long-press on the terminal, not a
keybar button, which is gone) and *what happens inside it* (handle-driven
`select()` calls instead of a native range). "모두 선택" selects the visible
viewport (`rows×cols`), not `selectAll()`'s full scrollback — buffer text you
can't see isn't what "select all" means to someone looking at a phone screen.

**Auto-copy and the Android long-press-to-paste fallback are now
touch-only-disabled** (`term/selection.js`) — `mouse.autocopyOnSelect` still
governs desktop drag-select unchanged, but on a coarse pointer neither path
runs at all, regardless of the setting. They were the second half of the
original complaint: a drag that copied without being asked, and a long-press
that sometimes fired the OS clipboard-read permission prompt and pasted
before the user had decided to select anything. Both were built for a native-
selection world that no longer exists on touch.

**Real-device verification (2026-09-24) found a real bug the CDP-emulated
testing above could not — the drag handles were completely non-functional on
actual touch hardware.** Tested via a physical Android phone (`adb`) with
Chrome remote debugging (`chrome://inspect` over USB) driving real
`Input.dispatchTouchEvent` calls and `elementFromPoint`/computed-style
inspection, not just screenshots. Root cause: xterm's `AccessibilityManager`
sets an **inline** `style="pointer-events:auto"` on `.xterm-accessibility`
(the vendor CSS's `.xterm-accessibility:not(.debug){pointer-events:none}`
never wins against an inline style) with `z-index:10` — one point above
`.vt-term-select-handles`' `z-index:5`. Both sit in the same stacking context
(`.vt-term-wrap` never establishes one), so the accessibility layer's hidden
row `<div>`s — not the visibly-on-top handle — won every hit-test over the
terminal text. Long-press-to-select still worked (that listener is on the
wrapper and only needs the event to *bubble* through, indifferent to which
descendant was the real target); handle-drag was dead (its listener is
attached directly to `.vt-term-handle`, which was never actually in the
bubble path of a touch aimed at it). Confirmed with an instrumented listener:
zero `pointerdown`/`pointermove` events on the handle before the fix, the
full sequence after. Fixed by raising `.vt-term-select-handles` to
`z-index:11`. **Also observed, not yet root-caused:** the very first
long-press's selection is confirmed live by `term.getSelection()`
immediately, but the blue highlight overlay sometimes doesn't visually paint
until a second selection-changing action (e.g. the first handle-drag
movement) — copying and dragging both work correctly regardless, so this is
cosmetic, not functional, but is unexplained and left as a known open item.

**The keybar (`term/keybar.js`) is one row by default now, not two.**
Previously `#keybar-top` (mic/queue/upload slots + collapse toggle) and
`#keybar-keys` (Esc/Ctrl/Tab/arrows + `| / ~ - n p`, horizontally scrolling)
were separate rows, and collapsing hid the *second* row entirely — which
meant the keys people reach for constantly (arrows, Esc, Ctrl) vanished along
with the ones they rarely touch. Queue and upload slots are gone too (both
have a home now that didn't exist when the keybar was built: 첨부 on the
bottom nav, and the command palette's `:` mode for queue) — freeing the row
for what actually needs to always be visible: `[mic] Esc Ctrl Tab ↑ ↓ ← →
[expand]`, nine items sharing one row (the seven text/arrow keys flex evenly,
~38px each at 390px — a first functional pass, not the final visual polish).
Collapsing now only hides `| / ~ - n p` (`#keybar-extra-keys`, a wrapping
flex row instead of the old horizontal scroll — nothing to scroll to
anymore), and the default flipped: a device that's never touched the toggle
now starts collapsed (`keybar.collapsed` schema `def` in `core/settings.js`),
since the row worth hiding by default changed meaning.

That collapse state moved from raw `localStorage` into `core/settings.js`'s
existing (until now unused) `keybar.collapsed` device-scoped key — one
storage instead of two that could drift. **Verified, and worth recording
because it was a real bug, not a hypothetical:** the first wiring read the
settings-change subscription callback as if it always received the full
settings snapshot. `settings.js`'s `set()` only broadcasts `{ [key]: value }`
for the one key that changed — so switching mobile tabs (which persists
`ui.mobile.home`) fired the keybar's listener with a payload that didn't
mention `keybar.collapsed` at all, the listener read that absence as `false`,
and the bar silently sprang open and **wrote that wrong value back to the
device's stored settings** — a real cross-feature data-corruption bug caught
only by testing tab switches after wiring the toggle, not by reasoning about
the code. Fixed by checking the key is actually present in the notification
before acting on it.

**2026-09-29 — keybar reworked again: two left rows (Ctrl·Alt·Shift pinned
on top, a horizontal scroll strip below) + inverted-T arrows on the right.** The user reported two failures: trying to swipe the row pressed
whatever key the finger landed on, and there was no way to hold a modifier
while pressing another key. Both came from keys firing on `pointerdown`. Now
the strip (`#keybar-scroll`, `touch-action:pan-x`) fires on **release**, and
cancels past an 8px slop or on the browser's `pointercancel` when it takes
over the scroll; the arrow cluster (`#keybar-arrows`) does not scroll and
still fires on press. Ctrl/Alt/Shift share one model — tap = next key only,
tap again = lock, held = applies while held (to keybar keys and soft-keyboard
input alike), and a held modifier that was used in a chord does not count as
a tap on release. The strip carries only keys the soft keyboard lacks (Esc
Tab ⇧Tab ^C Home End PgUp PgDn Del tmux prefix F1–F12); the character keys,
their long-press variants, the collapse toggle and `keybar.collapsed` are
gone, and so is the keybar mic slot (the user never used it and it cost a
column) — the mic now lives only in the dock / rail slot. Modifiers sit
outside the strip so a chord is always in the same place and a thumb that
drifts while holding one does not cancel it. Modifier state reads like a
Caps Lock light: a 5px lamp top-right — faint ring (off), accent outline +
ring (next key), filled key + solid lamp (locked). Keycaps use `--mono`. The
strip fades whichever edge has more keys (`.more-left`/`.more-right`) and
maps vertical wheel to horizontal. Chosen from three candidates rendered on
the real app (pinned row / two-row scrolling grid / slim single row).

**2026-09-29 — app bar redone (H1, picked from three rendered candidates) and
the keybar made optional.** The session name is the one focal element
(600 weight, `--fs-md`, no button chrome — the ▾ says it's pressable); the
device and position drop to a mono caption under it ("MacBook Pro M1 Pro ·
2/5"), which absorbed the separate `#vt-env-chip` (`layout/env-chip.js` now
only publishes `html[data-vt-machine]` + a `vt:machine` event for
`picker.js`). The bar is 52px on compact; the rule is written as
`:root, html[data-skin]` because the farshell/macos skin blocks pin
`--topbar-h:32px` at `html[data-skin=…]` specificity, which silently beat a
bare `:root` (the older coarse-pointer 44px rule still loses to it on those
skins). Two 44px icon buttons on the right: `#keybar-open-btn` (keyboard) and
search. The keybar is **closed by default** and only that button opens it —
it never opens itself when the soft keyboard appears (user decision); the
state is the device-scoped `keybar.open`. Closing also releases any latched
modifier, and text-selection mode still shows its copy/done row while
closed. Touch leaves `:hover` stuck after a tap, which made a closed toggle
look pressed — on `(hover:none)` only `aria-pressed` paints it.

**Quick open (step 5) is now a full-height bottom sheet on compact** instead
of the 60vh-capped card shared with the desktop layout — see § 6's "Default
screen redesign" for the full writeup (sessions moved out of the default
screen, recent-commands tracking, the two dead keymap shortcuts, and the
`vt-qo-body`/`vt-vw-body` scroll-clipping bug that caused the reported
"10개인데 3개만 보인다").

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
- **Badges** (`.vt-badge`): 1px `--color-line` border, no fill, `--radius-sm`.
  Only status badges get a status-colored border. A badge **counts** something —
  a diff count, an unread count, a port count.
  **A badge needs room of its own.** It sits in the flow, so it widens whatever
  holds it: an 18px `min-width` badge inside a 36px collapsed dock tab made the
  tab 38px wide and `overflow: hidden` cut the rest off. Where the container is
  a fixed strip, drop the number for a 6px `--color-acc` dot positioned
  `absolute` (contributing 0px of width) and let the tooltip say the count.
- **Tooltips** (`.vt-tip`, `js/ui/tooltip.js`): one `position: fixed` element on
  `body`, driven by `data-tip` (name) and optional `data-tip-sub` (the value —
  a count, a shortcut, a reason; `--font-mono`, `tabular-nums`, `--color-sub`).
  `data-tip-side` picks `top|bottom|left|right` and flips when the side doesn't
  fit. **Never the native `title` attribute**: its ~1s delay can't be tuned, it
  can't be themed across the 6 skins, and it does nothing under touch. 400ms to
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
  terminal.** `info`/`success` take over the 24px HUD strip (`#vt-hud`), which
  lives in normal flow and therefore hides nothing — the terminal's own message
  line, like tmux's status line or vim's echo area. Only `warn`/`error` float,
  as a card just above the HUD on the right, clear of the prompt line at the
  bottom left. In compact (<720px) there is no HUD, so everything is a card,
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
  5px radius step. Added in 2.1.6, when seven separate copies of that
  `color-mix` were folded into one component.
- **Tabs (header, dock)**: active = a 2px `--color-acc` bottom underline +
  `--color-acc-surface` background. Inactive = `--color-sub` text.
- **Panes** (`.vt-pane`): flush, like iTerm2. No border, no radius, no `gap`
  between siblings, no padding on `#terminal-container` — the boundary is the
  1px `--color-line` divider and nothing else. `--term` background and
  `overflow: hidden` stay (the terminal lives inside the pane, so without the
  clip it spills over its neighbour while a pane shrinks).
  A bordered, gutter-separated box was tried first, to make xterm's
  cell-quantization remainder read as the pane's own padding. It did the
  opposite — it *added* 5px of gutter to the empty strip it was meant to
  explain. The remainder is not the kind of thing you fix by adding space.
- **Pane sizes snap to whole terminal cells** (`layout/snap.js`). A split pins
  its **first** child to a multiple of the cell (`flex: 0 0 568px`) and lets
  the second take the rest; both can't be multiples at once, because
  `multiple + 1px divider + multiple` isn't one. What that buys is that the
  leftover stops appearing at *every* boundary and collects at one outer edge
  instead. Vertical snapping measures from below the 24px head, so a pane is
  `head + rows × cellHeight`. Dragging a divider therefore moves in whole
  columns. Before this, a half-drawn column showed up as a blank strip
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

## 11. Accessibility

- **Touch targets:** under a coarse pointer, the bottom nav and keybar
  buttons keep a 44px tap area. A fine pointer keeps a tighter density.
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
| `styles/theme/skins.css` | Color remapping for the other 5 skins |
| `styles/layers/components.css` | `.status-dot` — the home for new components |
| `styles/layers/legacy.css` | Every existing component + `.vt-*` overlays + the rail/dock/HUD frame's CSS (unlayered) |
| `frontend/index.html` | Boot theme script (anti-FOUC), login gate, `#vt-chrome` mount point, mobile bottom nav, keybar |
| `frontend/js/layout/surface.js` | Terminal placement (which pane holds which session) · refit gate · `#vt-term-stage` waiting room |
| `frontend/js/core/signals.ts` | Bridge from `core/store.js`/`layout/store.js` to Solid signals |
| `frontend/js/core/types.ts` | Shared TS types |
| `frontend/js/shell/Rail.tsx` | Worktree rail, 252px (N36) |
| `frontend/js/shell/Dock.tsx` | Dock frame, 392px (N35) |
| `frontend/js/shell/Hud.tsx` · `hud-data.ts` | HUD, 24px (N34) |
| `frontend/js/shell/HeaderExtras.tsx` | Workspace chip · connected screens (N37) |
| `frontend/js/shell/Fleet.tsx` | Mobile fleet home (N38) |
| `frontend/js/shell/Palette.tsx` · `palette-data.ts` | Command palette, 7+1 modes (N5/N40/N46) |
| `frontend/js/layout/resize-overlay.js` | px / cols×rows caption while dragging a divider (N43) |
| `frontend/js/layout/tree.js` | Pane tree — leaf `kind: 'terminal'\|'viewer'\|'panel'` (N4) |
| `frontend/js/layout/breakpoints.js` | The 4 responsive-tier boundaries (single source) |
| `frontend/js/layout/dom-ids.js` | Tree node id → DOM element id, in one place (the maker and the finder both read it) |
| `frontend/js/layout/snap.js` | Cell-multiple snapping for pane sizes — pure, unit-tested |
| `frontend/js/layout/panes.js` · `compact.js` · `dnd.js` | Pane-tree rect computation · compact mode · drop zones |
| `frontend/js/core/settings.js` | Server-backed settings store — global/device scope (N3) |
| `frontend/js/core/keymap.js` | Keymap registry |
| `frontend/js/panels/viewer/scm.js` | Dock source-control tab renderer (read-only) |
| `frontend/js/ui/icons.js` | Inline SVG icon registry (the only icon source) — includes the 5 agent marks |
| `frontend/js/ui/tooltip.js` | The one tooltip layer — `data-tip` / `data-tip-sub` / `data-tip-side`, replaces native `title` |
| `frontend/js/term/xterm-setup.js` | xterm instance creation, applies `getVtXtermTheme()` |
| `server/usage/base.py` · `clauth.py` · `counter_jsonl.py` · `ollama.py` | LimitProvider/CounterProvider (N10/N41) |
| `server/device_settings.py` | Device-scoped settings storage (N3) |
| `frontend/js/voice/` | Recording/STT/TTS, media keys, voice-only mode — built as its **own** lib entry (`frontend/dist/voice.js`), lazy-loaded only when the voice capability is on |
