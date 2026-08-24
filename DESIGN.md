# FarShell Design System

[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./DESIGN.ko.md)

## Overview — 5 themes, unified top bar layout

The FarShell frontend is a mobile-first web terminal with **5 selectable themes**.
Themes switch via the `<html data-skin="...">` attribute, and **every color is
referenced through CSS variables (tokens) only** — no hardcoded hex values.
Even overlays that JS creates dynamically inherit tokens through `.vt-*` classes.

| Skin | Identity | Default |
|------|--------|--------|
| `macos` | iTerm2/Terminal.app — traffic lights, SF font, system blue, rounded window | ✅ Default |
| `catppuccin` | The original pastel look — no chrome, lavender accent | |
| `windows` | Windows Terminal — caption buttons, Cascadia, Fluent blue, sharp corners | |
| `vscode` | VS Code integrated terminal — dark gray, Fluent blue accent, sharp UI | |
| `notepad` | Notepad/paper feel — the only **light theme**, warm off-white background | |

Switching: top `⋯` menu → theme chips (`#theme-row`, 5 of them). Saved to
`localStorage['vt-skin']` and restored on revisit. An inline `<head>` script
locks in `data-skin` before paint at boot time (prevents FOUC).

## Layout (2026 redesign)

Moved from the old 3-zone layout (top tab bar · terminal · bottom voice bar) to a
**unified top bar**. Removing the bottom bar reclaimed vertical space for the
terminal. Voice input is no longer a separate floating FAB — it's now a
**pill button inlined into the top bar**: the `#voice-bar` container has been
removed from the HTML, and `#mic-btn-wrap` now lives on the same row as the
other icon buttons inside `#topbar`.

```
┌───────────────────────────────────────────────┐
│ ◉◉◉  [tab][tab][+]   ( 🎤 Voice input ) 🔍 ⊞ ⋯ (⚊▢✕) │ ← #topbar (fine 38px / coarse 44px)
├───────────────────────────────────────────────┤
│                                                 │
│                 #terminal-container             │
│                 (xterm, per-theme ANSI)          │
│                                     ┌──────┐    │
│                                     │status│    │ ← #mic-status (top-right, pill below the top bar)
│                                     └──────┘    │
└───────────────────────────────────────────────┘
```

- **`#topbar`**: traffic lights (macOS only) · tabs (`#tabs`, inserted before
  `#add-btn`) · session jump dropdown (`#voice-session-picker`, narrow screens
  only) · voice input (`#mic-btn-wrap`, inline pill) · `🔍 search` ·
  `⊞ Grid` (`#grid-toggle`) · code viewer (`#viewer-toggle`) · `⋯ more`
  (`#more-btn`) · caption buttons (windows skin only).
- **`#more-menu`**: tmux sessions · open on Mac too (checkbox) · voice-only
  mode · earbud media keys · file upload · code viewer · prompt queue · ports
  · guide · push notifications · drag-to-copy · 5 theme chips.
- **`#mic-status`**: a status pill pinned bottom-right below the top bar
  (auto-hides when empty). In voice-only mode, `#mic-btn-wrap` enlarges
  (50px svg) and becomes a full-screen microphone. `grid.js` hides every
  `.needs-voice` element entirely when `/api/capabilities` reports voice is
  not installed.

## Tokens (CSS variables)

Defined in `html[data-skin="..."]` blocks in `css/app.css`. Each skin
redefines them.

| Token | Purpose |
|------|------|
| `--win` | App background |
| `--bar` | Top bar background (also used for the mobile theme-color meta) |
| `--tab` / `--tab-active` / `--tab-active-txt` | Tabs |
| `--term` | Terminal background (matches the xterm background) |
| `--txt` / `--sub` | Body / secondary text |
| `--acc` / `--acc-ink` | Accent color / text on accent (FAB, active state, links) |
| `--menu` / `--menu-hover` / `--line` | Menu & dividers |
| `--ok` / `--warn` / `--err` / `--info` | Semantic colors (disconnected, error, success) |
| `--crust` | Grid card preview background |
| `--wrad` / `--trad` | Window / tab corner radius |
| `--ui` / `--mono` | UI / monospace font stacks |

### Typography — OS-native (intentional)

Each skin uses its OS's native system font. `system-ui` here isn't a "gave up
on typography" signal — it's the **authentic choice for mimicking iTerm2/Windows
Terminal**.

- macOS: `-apple-system, "SF Pro Text"` (UI), `ui-monospace, "SF Mono", Menlo` (terminal)
- windows: `"Segoe UI"` (UI), `"Cascadia Code", "Cascadia Mono", Consolas` (terminal)
- catppuccin: `system-ui` (UI), `ui-monospace, "SF Mono", Menlo, Consolas` (terminal)

## xterm.js terminal theme

What makes something feel like "iTerm2" versus "Windows" isn't the window
chrome — it's the **terminal's own background + 16-color ANSI palette**.
`js/theme.js`'s `VT_XTERM_THEMES` defines a complete per-skin palette:
`background/foreground/cursor/selection + black..white + brightBlack..brightWhite`.

- macos: deep black (#101012) + macOS system colors (red #ff453a, green #32d74b, blue #0a84ff …)
- catppuccin: #1e1e2e + Catppuccin Mocha palette
- windows: **official Campbell palette** (#0c0c0c, red #c50f1f, blue #0037da …)
- vscode: #1e1e1e + VS Code integrated terminal default palette
- notepad: the only light background (#fffefb) + blue cursor (#0060df) — a dedicated palette tuned for the light background

`addSession()` applies `getVtXtermTheme()` on creation, and on theme switch
`setVtSkin()` immediately updates `term.options.theme` for every open terminal.

## Components

### Voice button (`#mic-btn-wrap`)
- An inline pill in the top bar (`.tbtn.mic`), `--acc` background + label text.
  At 440px and below, the label is hidden and it shrinks to a 32px square
  icon button. In voice-only mode it expands into a large centered button
  (50px svg) — no longer a fixed bottom-right FAB.
- voice.js contract: a `.label` child text node + a `.recording` class
  (turns `--err` + pulse while recording).
- Status is shown as text in `#mic-status` (a pill fixed below the top bar,
  right side): "Recording — tap to stop" · "Processing..." · "Microphone
  permission required" · `"<recognized text>"` · "Recognition failed" ·
  "Send failed".

### `⋯` menu (`#more-menu`) / popup
- `--menu` background, `.mi` items (hover: `--menu-hover`), `.msep` dividers,
  `.mlabel` section headers.
- Clicking an action closes the menu automatically. Toggles (checkbox/
  voice-only/earbuds) stay open.

### Dynamic JS overlays (`.vt-*` classes, inherit tokens)
- `.vt-onboarding` — empty state when there are 0 sessions (primary actions:
  tmux session / plain terminal).
- `.vt-overlay` — full-screen "server disconnected" state + `#conn-status` pill.
- `.vt-menu` / `.vt-menu-item` — tmux session dropdown.
- `.vt-toast` (`.ok`/`.err`/`.info`) — notification/upload/agent toasts.
- `.vt-card` / `.card-title` / `.card-cmd` / `.card-preview` / `.vt-grid-empty` — Grid view.
- `.vt-banner` — safe-mode banner.

## Responsiveness & accessibility

- Mobile-first. On narrow screens (<720px), a session-management button
  showing the current session name opens a bottom sheet for switching,
  renaming, and closing sessions individually. This stays available even
  when the tab strip is squeezed down to 0px.
- Touch targets: on coarse pointers, `--topbar-h` becomes 44px, and `.tab`
  and the close button also use a real 44px height. Icon buttons keep a
  visual size of 30px while a `::before` pseudo-element provides a full
  44px tappable area.
- Keyboard: `#add-btn` responds to Enter/Space, `⋯` has
  `aria-haspopup`/`aria-expanded`, search uses Ctrl/Cmd+F, Grid/search close
  on Esc, and `:focus-visible` gets an outline (`--acc`).
- Screen readers: icon buttons have `aria-label`, `#mic-status` has
  `role="status" aria-live="polite"`.
- `prefers-reduced-motion`: disables all animations/transitions.
- safe-area-inset: top/bottom padding applied (notches/gesture bars).

## File map

| File | Responsibility |
|------|------|
| `frontend/index.html` | Layout markup, boot-time theme script, `⋯` menu toggle |
| `frontend/css/app.css` | The 3 token sets + all components + `.vt-*` overlays |
| `frontend/js/theme.js` | Skin switching, localStorage, xterm theme definitions/sync |
| `frontend/js/terminal.js` | Session/tab/PTY, applies `getVtXtermTheme()`, creates overlays |
| `frontend/js/picker.js` | Mobile session-management sheet, toasts, file upload |
| `frontend/js/grid.js` | Live preview grid, capability gating, safe-mode banner |
| `frontend/voice.js` | Recording/STT/TTS, media keys, voice-only mode (loaded dynamically when capability is ON) |
