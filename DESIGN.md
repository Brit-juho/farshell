# FarShell Design System

[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./DESIGN.ko.md)

> **As of v2.0.0.** The September 2026 frontend restructure changed the layout,
> the tokens, and every entry point — see [ARCHITECTURE.md](./ARCHITECTURE.md) §2
> for the module map.

---

## 0. Design principles

These are the rules this project actually holds itself to. Read this before
drawing a new screen.

### Restraint is taste

Gradients, animations, glassmorphism, decorative icons — none of them go in
just because they *can*. Every visual element has to earn its place, and the
default is always the simpler thing.

### Color — 70/20/10

Four neutral background steps (`--bg-0..3`) are the 70%, three text steps are
the 20%, and the accent is the 10%.

- **One solid accent per screen — the primary action.** If the active rail item,
  a badge, and a primary button all fill with the accent at once, none of them
  reads as primary.
  → The active rail item states itself through background elevation; the accent
  survives only as the icon color.
- Don't give every element its own tinted background. Whitespace and layout
  separate things first.
- Don't add a border where a background step already separates.
- **The test:** squint at the page. If it's one blob of similar color, it failed.

### Icons

- **Never trap an icon in a filled rounded square or circle.** Avatars and app
  icons are the only exceptions.
- If the icon doesn't carry information the user needs, delete it.
- Icons work in actions (buttons, nav). They are not decoration on info cards.
- Don't mix libraries — the inline SVG registry in
  `frontend/js/ui/icons.js` is the single source.
- **No emoji as UI assets.** User-generated content (terminal output) is exempt.

### Typography

- Sans-serif by default (`--font-ui`). Serif is never the "elegant" default for
  a hero headline.
- Build hierarchy from size, weight, and color — not from stacked containers.

### Glass, gradients, shadows

- `backdrop-filter: blur()` only where a skin **explicitly imitates an OS** —
  just `macos` (translucent top bar) and `windows` (Windows Terminal acrylic),
  and only on the top bar at that. Cards and panels in the default `farshell`
  skin are all opaque.
- No gradients on text or buttons.
- **No glowing shadow of the button's own color behind it.** Shadows are neutral,
  low-opacity, and used to state elevation (`--shadow-e1..e3`).
- One exception: a 0-blur accent ring is not a shadow, it's a **focus ring** —
  an accessibility affordance, and it stays.

### Hierarchy — fewer nested boxes

- No card inside a card. If the inner card could just be content in the parent,
  drop it.
- Tone secondary information down (`--sub`/`--muted`) instead of boxing it off.
- Every boundary must answer: what does this line help the user understand?

### Motion

- No entrance animations (fade-in, slide-up) on content by default.
- No hover states that combine several transforms at once.
- 120–300ms, subtle, and only with a purpose (feedback, state change, spatial
  orientation).
- **One moving thing per screen.** Of the status dots, only `waiting` breathes —
  if every session in a list animates, nothing stands out.
- Under `prefers-reduced-motion` all animation and transition is disabled
  globally, and `waiting` conveys the same information with an outline instead
  of a pulse.

### Checklist before you start

- [ ] No icon trapped in a colored box
- [ ] No emoji used as a design element
- [ ] No default serif hero headline
- [ ] No glassmorphism that wasn't asked for
- [ ] No gradients on text or buttons
- [ ] No colored glow behind a button
- [ ] Accent holds 70/20/10 (solid accent = one primary action)
- [ ] No purposeless card nesting
- [ ] No slow entrance animation on content
- [ ] No `border-left` combined with `border-radius`
      (the bar gets clipped at the corner — use `box-shadow: inset` for a left accent)
- [ ] You can name the #1 thing to look at within half a second

---

## 1. Skins — six of them

Switched via `<html data-skin="...">`. **Every color goes through a token** —
hardcoded hex is banned. Overlays that JS builds at runtime inherit tokens
through their `.vt-*` classes.

| Skin | Identity | Default |
|------|----------|---------|
| `farshell` | Near-black (#0a0a0b) + **a single amber accent** (#f0a860). Our own face; imitates no OS | ✅ default |
| `macos` | iTerm2/Terminal.app — traffic lights, SF fonts, system blue, rounded window | |
| `catppuccin` | Pastel — no chrome, lavender accent | |
| `windows` | Windows Terminal — caption buttons, Cascadia, Fluent blue, square window | |
| `vscode` | VS Code integrated terminal — dark gray, Fluent blue accent, square UI | |
| `notepad` | Notepad/paper feel — the only **light theme**, warm off-white background | |

Switch from rail ⚙ settings or the palette's theme chips (`Mod+K`). At boot an
inline `<head>` script pins `data-skin` before first paint (no FOUC).

## 2. Tokens

| File | Holds |
|------|-------|
| `styles/theme/tokens.css` | Structural tokens (spacing, fonts, sizes, radii, shadows, easing, breakpoints) + the color tokens of the default `farshell` skin. Tailwind v4 `@theme static`, so it compiles to real `:root` custom properties |
| `styles/theme/skins.css` | The other five skins remapping the same color tokens |
| `styles/layers/components.css` | `.status-dot` — where new components go |
| `styles/layers/legacy.css` | Every component + the `.vt-*` overlays. Unlayered, so it always wins |

Vite builds all four into `frontend/dist/app.css`.

### Color tokens (farshell)

The contract is **17 colors** (4 background + 2 divider + 3 text + 3 accent +
5 state), plus two terminal colors handed to xterm and one surface-state color.

| Token | Value | Purpose |
|-------|-------|---------|
| `--color-bg-0..3` | `#0a0a0b` `#131316` `#1a1a1e` `#232328` | Four background steps — separate elements with these, not with borders |
| `--color-line` / `--color-line-strong` | `rgb(255 255 255 / .08)` / `.16` | Two divider weights |
| `--color-txt` / `--color-sub` / `--color-muted` | `#eeeef0` `#9a9aa2` `#7c7c84` | Three text steps |
| `--color-acc` / `--color-acc-dim` / `--color-acc-ink` | `#f0a860` `#b3803f` `#17140f` | Accent · dimmed accent · text on accent |
| `--color-st-{idle,working,waiting,done,error}` | `#7c7c84` `#4fd1a5` `#f0a860` `#5b8def` `#f06868` | The four agent states + error |
| `--color-term-bg` / `--color-term-fg` | `#0a0a0b` / `#eeeef0` | xterm background/foreground |
| `--color-acc-surface` | `var(--color-acc)` | The fill that `acc-ink` text sits on (primary buttons, badges, the mic button). Defaults to the accent itself; **only macos overrides it to `acc-dim`** — white on Apple's system blue measured 3.65:1 |
| `--color-surface-active` | `rgb(255 255 255 / .18)` | The "selected right now" surface. Twice the hover overlay — reusing the hover value makes active indistinguishable from hover on a pointer device. The accessibility requirement is carried by the accent bar, not by this |

### Contrast rules

The bar differs by role. Forcing `muted` to 4.5:1 too would collapse it into
`sub` and destroy the three-step ramp, so we split it the way WCAG actually does.

| Token | Bar | Why |
|---|---|---|
| `txt` | **4.5:1** | Body text (WCAG 1.4.3 AA) |
| `sub` | **4.5:1** | Secondary, but still text people read |
| `muted` · `st-*` | **3:1** | UI components and graphical objects (WCAG 1.4.11) |

The bar has to hold on **all four background steps** — a color passing on `bg-0`
and failing on `bg-3` is exactly what happened here.

**Measured (worst case across the four backgrounds, per skin):**

| Skin | `txt` | `sub` | `muted` |
|---|---|---|---|
| farshell | 13.50 | 5.60 | 3.78 |
| macos | 9.12 | 4.52 | 3.02 |
| catppuccin | 6.31 | 4.52 | 3.00 |
| windows | 8.82 | 5.03 | 3.12 |
| vscode | 8.58 | 4.54 | 3.03 |
| notepad | 11.61 | 4.52 | 4.31 |

Until 2026-09-08, **28 combinations were below the bar**. Only farshell had been
built from measured contrast; the other five carried each OS's colors over
verbatim — macos `sub` sat at 3.49:1 on bg-3 and vscode `sub` at 3.73:1 on bg-1,
genuinely unreadable outdoors or on a bright screen.

Fixing it moved **lightness only, keeping hue and saturation exactly.** Imitating
the OS is the point of those skins, and shifting the hue would stop it being an
imitation. Seven values changed, with H and S identical to the decimal (e.g.
macos `sub` `#8e8e96`→`#a3a3a9`, H 240° S 4% unchanged). `st-idle` equals `muted`
in all six skins, so it moves with it.

`frontend/tests/tokens-contrast.test.js` recomputes this table in CI on every run.

### Structural tokens

- **Spacing:** one scale, `--spacing: 4px`. Every gap is `calc(var(--spacing) * N)`.
- **Type sizes:** `--text-2xs` (10.5) through `--text-2xl` (28), seven steps.
- **Radii:** `--radius-sm` (6) / `md` (10) / `lg` (14) / `full`. Nothing outside
  that four-step hierarchy.
- **Shadows:** `--shadow-e1..e3`, all neutral (low-opacity black). No colored shadows.
- **Motion:** `--dur-fast` (120ms) / `--dur-base` (200ms), `--ease-out-quint`.
- **Breakpoints:** `--breakpoint-regular` (720px) / `--breakpoint-wide` (1280px) /
  `--breakpoint-xwide` (1600px, 2.1.0 — N4 unlimited pane cap starts here).
  The JS side keeps **exactly one copy** in `frontend/js/layout/breakpoints.js`.

### Code-derived values (machine-checked)

For the values below **the code is the single source of truth** and this document
merely transcribes them. A transcription always drifts eventually, so
`check_design_md()` in `scripts/check_docs.py` compares this table against the
code in CI (I3). Why a table rather than the prose: a bare `7` or `720` appears
somewhere in the document no matter what, so scanning prose passes even when the
value is wrong — measured, not assumed.

| Key | Value | Source |
|---|---|---|
| `skins` | `farshell, macos, catppuccin, windows, vscode, notepad` | `frontend/js/theme.js` — `VT_SKINS` |
| `breakpoints` | `720/1280` | `frontend/js/layout/breakpoints.js` — `COMPACT_MAX`/`REGULAR_MAX` |
| `pane-cap` | `2/4/6` | `frontend/js/layout/dnd.js` — `tierCap()` |
| `rail-items` | `6` | `frontend/index.html` — `.vt-rail-btn` count |
| `agent-states` | `5` | `server/agent_status.py` — `STATUSES` (`error` reserved) |
| `color-bg-0` | `#0a0a0b` | `styles/theme/tokens.css` |
| `color-acc` | `#f0a860` | `styles/theme/tokens.css` |
| `color-txt` | `#eeeef0` | `styles/theme/tokens.css` |

### Legacy aliases

`legacy.css`'s `--win`/`--bar`/`--acc`/`--txt`/`--sub`/`--line`/`--ok`/`--err`/
`--crust`/`--trad` are aliases pointing at the `--color-*` tokens above.
Rewriting the several hundred consumers to `var(--color-*)` is a completely
different job from moving where the values come from, so it hasn't been done —
**new code uses `--color-*` directly.**

### Typography — OS-native, on purpose

`farshell` uses the fonts **bundled in the repo**: IBM Plex Sans (UI) and IBM
Plex Mono (terminal), from `frontend/vendor/fonts/`. `--mono` prefers JetBrains
Mono when it's installed locally and falls back to the bundled Plex Mono —
either way there is no network request.

The OS-imitating skins use each OS's system font. `system-ui` there is not a
"we gave up on typography" signal but **an authentic choice for imitating
iTerm2 / Windows Terminal**.

- macOS: `-apple-system, "SF Pro Text"` (UI), `ui-monospace, "SF Mono", Menlo` (terminal)
- windows: `"Segoe UI"` (UI), `"Cascadia Code", "Cascadia Mono", Consolas` (terminal)
- catppuccin: `system-ui` (UI), `ui-monospace, "SF Mono", Menlo, Consolas` (terminal)

## 3. Layout — three columns

The `⋯` overflow menu is **entirely gone** in 2.0. Two paths split its job
(ADR-8): the **left rail is the pointer path**, the **command palette (`Mod+K`)
is the keyboard path**. They expose **the same things**, so learning either one
is enough.

```
┌──┬────────────────────────────────────────┬──────┐
│  │  [tab][tab][+][⌄]              search  │      │ ← #topbar (fine 38px / coarse 44px)
│r ├────────────────────────────────────────┤ right│
│a │ ┌──────────────┬───────────────────┐   │ rail │
│i │ │  pane        │  pane             │   │(wide │
│l │ │  (xterm)     │  (xterm)          │   │ only,│
│  │ ├──────────────┴───────────────────┤   │ usage│
│  │ │  pane                            │   │ gauge│
│  │ └──────────────────────────────────┘   │      │
└──┴────────────────────────────────────────┴──────┘
   ↑ #vt-rail (48px, ≥720px only)
```

- **`#topbar`**: traffic lights (macos only) · tabs (`#tabs`) · `+` (new session)
  · `⌄` (session profiles) · a "sessions" button (narrow screens only) · quick
  open (`#palette-toggle`) · caption buttons (windows only).
  **The mic moved out of the top bar in L7** — that reclaimed one accent button,
  leaving the top bar as tabs, `+`, and the palette. The mic's desktop home is
  `#vt-rail-mic-slot`; on touch devices it moves into the keybar.
- **`#vt-rail`** (48px, ≥720px): sessions · files · queue · snippets · ports ·
  usage · ⚙ settings — seven items. Hidden entirely in compact, where the palette
  and bottom sheets take over. Badges are `.vt-rail-badge` (accent, 15px).
  **The active state speaks through two channels**: a 3px amber bar on the left
  (`::before`) carries the accessibility requirement (WCAG 1.4.11, 3:1 for state
  identification — measured 3.05–9.25:1 against the rail background), and a
  dedicated `--color-surface-active` token carries the separation from hover
  (measured 1.31–1.39:1). The icon is `--txt`: an accent-colored icon drops to
  1.82–2.14:1 on macos and vscode. **Only `session` and `settings` ever get
  `.active`** (`layout/rail.js:23` `PANEL_ITEMS`) — the other five open modals,
  not panels.
- **Split panes**: the terminal area is a **binary tree** of panes. Split from
  the pane header, or by dropping a tab on a pane edge (five drop zones); the
  divider drags. When `root` is a single leaf the pane header hides and you get
  **the same full-screen terminal as 1.7.0**.
- **Right rail**: wide (≥1280px) only. Home of the usage gauge — it disappears
  entirely when there's no source.

### Responsive tiers

| Tier | Width | Pane cap | Rendering |
|------|-------|----------|-----------|
| compact | <720px | 2 | On touch, **one pane at a time** full-screen with a `· 1/2` position marker in the header; swipe left/right to move |
| regular | 720–1279px | 4 | The split tree as-is. Panels overlay (they don't push the pane behind) |
| wide | 1280–1599px | 6 | Split tree + right rail. Panels push |
| xwide | ≥1600px (2.1.0) | unlimited (N4) | Same as wide, plus no pane-split cap |

Past the cap the split buttons are **disabled with the reason in their tooltip** —
never silently doing nothing. The boundary values live in one place,
`layout/breakpoints.js`.

## 4. Agent state — four states

The server decides a status per session and **the frontend only draws it**.

**The contract is five values** — `server/agent_status.py:46`,
`STATUSES = (IDLE, WORKING, WAITING, DONE, ERROR)`. Four of them have an entry
path today; `error` is marked *"reserved — no entry path in 2.0"* on line 44 of
the same file. Its color token (`--color-st-error`) and sort priority
(`_URGENCY`) are already in place. So "four states" is **how many you can
observe right now**, while anything consuming the status must handle all five. Tabs, pane headers, the rail list (sorted so
what needs you is on top), the favicon, and the app icon badge (`waiting` count)
all read the same value.

- The component contract is one selector: `.status-dot[data-state="..."]`
  (`styles/layers/components.css`), colored by `--color-st-*`.
- **Only `waiting` breathes** — the unanimous conclusion from the reference
  survey: the highest-value state is not running or finished, it's
  **waiting for your input**.
- `working` on a card or tab animates **a single 8px dot**, not the whole card
  (`opacity`/`transform` are compositor-only, so no repaint — animating
  `box-shadow` on the card would repaint the whole grid for as long as it's open).
- None of this arrives without the Claude Code hooks (`fsh hooks install`).
  `fsh doctor` and Settings → About report when they're missing.

## 5. xterm.js terminal theme

What makes something feel like iTerm2 versus Windows is not the window chrome —
it's **the terminal's own background plus its 16 ANSI colors**.
`VT_XTERM_THEMES` in `js/theme.js` defines a complete palette per skin:
`background/foreground/cursor/selection + black..white + brightBlack..brightWhite`.

- farshell: near-black (#0a0a0b) + amber cursor
- macos: deep black (#101012) + macOS system colors (red #ff453a, green #32d74b, blue #0a84ff …)
- catppuccin: #1e1e2e + the Catppuccin Mocha palette
- windows: the **official Campbell palette** (#0c0c0c, red #c50f1f, blue #0037da …)
- vscode: #1e1e1e + the VS Code integrated terminal defaults
- notepad: the only light background (#fffefb) + blue cursor (#0060df)

`addSession()` (`js/term/xterm-setup.js`) applies `getVtXtermTheme()` at creation,
and on a skin switch `setVtSkin()` (`js/theme.js`) immediately updates
`term.options.theme` on **every open terminal**.

## 6. Components

### Command palette (`Mod+K`)
The single confirmation entry point spanning sessions, files, and commands
(ADR-8). **Each command shows its current key binding**, read from the keymap
registry (`js/core/keymap.js`), so a rebound key shows up as rebound. A `/`
prefix searches within the terminal.

### Settings (`Mod+,` / rail ⚙)
Settings live in **`/api/workspace.settings` on the server**, not in
`localStorage` — a change on your phone shows up on the Mac. Key bindings are
rebindable, and **`passthrough` hands a key back to the terminal** (that's how
you get `Mod+F` back as the shell's `forward-char`). Turning off "forward mouse
events to the app" in the mouse section makes drag-select always work, even
under vim/tmux mouse mode.

### Session card (`.vt-card`)
Shared by the rail session panel and the live preview. Three levels of
hierarchy: title (brightest, largest) > preview (mid-tone `--txt2`, the actual
content) > metadata (`--sub`).

- Already open as a tab: `box-shadow: inset 3px 0 0 var(--acc-dim)`. A
  `border-left` would be clipped by the card's radius and would also shift the
  layout by 3px.
- `working`: fixed border color + a single 8px pulsing dot. `done`: border color
  + a `✓` badge. Neither adds **a colored ring shadow** — the border color
  already says it.

### Toast (`.vt-toast`)
Three kinds: `.ok` / `.err` / `.info`. Notifications, uploads, and agent events
all go through this one.

### Runtime overlays (`.vt-*`, inheriting tokens)
- `.vt-onboarding` — the zero-session empty state. The hero icon is `--muted`;
  the accent belongs to the one primary action (`.vt-btn-primary`).
- `.vt-overlay` — full-screen server-disconnected state + the `#conn-status` pill.
- `.vt-viewer-backdrop` / `.vt-viewer-card` — the modal skeleton (backdrop,
  close, Esc, click-outside) shared by the code viewer, queue, ports, and guide.
  The same rules turn it into a bottom sheet in compact.
- `.vt-banner` — the safe-mode banner.

## 7. Accessibility

- **Touch targets:** on a coarse pointer `--topbar-h` becomes 44px and `.tab`
  plus the close button take a real 44px height. Icon buttons keep a 30px visual
  size and claim a 44px tap area via `::before`. Fine pointers keep the 38px/28px
  density.
- **Narrow screens:** even when `#tabs` is squeezed to 0px, the "sessions" button
  opens a bottom sheet that still offers switching, renaming, and closing.
- **Keyboard:** `Mod+K` palette, `Mod+,` settings, `Ctrl/Cmd+F` search, Esc to
  close, Enter/Space on `#add-btn`, `aria-haspopup`/`aria-expanded`,
  `:focus-visible` outline (`--acc`).
- **Screen readers:** `aria-label` on icon buttons, `#mic-status` is
  `role="status" aria-live="polite"`.
- **`prefers-reduced-motion`:** all animation and transition disabled globally;
  the `waiting` dot falls back to an outline.
- **safe-area-inset:** applied as top/bottom/left padding (notch, gesture bar).

## 8. File map

The full module map is in [ARCHITECTURE.md](./ARCHITECTURE.md) §2. This table is
deliberately not exhaustive — it lists only the files tied to *visual* design.

| File | Responsibility |
|------|----------------|
| `styles/theme/tokens.css` | Structural tokens + farshell's color tokens (`@theme static`) |
| `styles/theme/skins.css` | Color remap for the other five skins |
| `styles/layers/components.css` | `.status-dot` — where new components go |
| `styles/layers/legacy.css` | Every component + the `.vt-*` overlays (unlayered) |
| `frontend/index.html` | Layout markup, boot theme script (FOUC prevention), login gate |
| `frontend/js/theme.js` | Skin switching, xterm theme definitions and sync |
| `frontend/js/design/state-classes.js` | State → class/label mapping |
| `frontend/js/layout/breakpoints.js` | The responsive tier boundaries (single source) |
| `frontend/js/layout/panes.js` · `compact.js` · `dnd.js` | Pane tree rendering, compact mode, drop zones |
| `frontend/js/layout/rail.js` | Left rail + embedded panels |
| `frontend/js/core/settings.js` · `keymap.js` | Server settings store · keymap registry |
| `frontend/js/ui/icons.js` | Inline SVG icon registry (the only icon source) |
| `frontend/js/ui/toast.js` | Unified toast |
| `frontend/js/ui/favicon.js` | State-reflecting favicon |
| `frontend/js/term/xterm-setup.js` | xterm instance creation, `getVtXtermTheme()` |
| `frontend/js/term/conn-overlay.js` | Full-screen server-disconnected overlay |
| `frontend/js/picker.js` | Mobile session sheet, file upload |
| `frontend/js/voice/` | Recording/STT/TTS, media keys, voice-only mode — built as a **separate** lib entry (`frontend/dist/voice.js`) and lazy-loaded only when the voice capability is on |
