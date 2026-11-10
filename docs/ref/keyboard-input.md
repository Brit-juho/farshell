# Keyboard input

## Processing order

`core/platform.js` provides the OS primary modifier and the settings storage profile. Mac resolves `Mod` to Command, everything else to Control. An explicit Meta stays distinct from Control even on non-Mac.

`core/keyboard-router.js` decides each event once, in the document capture phase, and stores the result in a WeakMap. The order is recording → top-most scope → editing surface → app → terminal. IME / Dead / AltGraph events are never handled as app shortcuts. For app actions such as split and close, key repeat is consumed but does not run the action again.

`core/keymap.js` owns action definitions, registration, binding storage and display. Letter/digit app combos are matched by position through `code`. The typed character is kept as `key`, and symbol combos are matched by their logical character. So Cmd+Option+D stays an app combo even when Option turns `d` into `∂` or the input layout changes. Events without `code` fall back to `key`.

`term/keyboard-input.js` uses the decision already stored for the same event and never re-matches app bindings. Legacy keys and IME are delegated to xterm; only negotiated extended keys are encoded as CSI u. kitty advertises only disambiguation flag 1, the one it implements. A non-ASCII Option combo whose base key can't be determined is not guessed; it is delegated to xterm.

## Delivery target and settings

| Mode | Behavior |
|---|---|
| App action | One app action, PTY delivery blocked |
| App + terminal | One app action, plus one forwarded event to the original terminal target |
| Terminal only | Delivered to the original terminal with no app action |

`passthrough=true` is kept as App + terminal. The forwarded event carries its own terminal decision, so it never re-runs the app action. If the original session or connection changes, or the target closes, the event is not sent to another active session.

The global `keymap.overrides` is kept for read compatibility. On the first edit its value is copied into `keymap.bindingsV2` as `{version:2, bindings:{...}}` and stored in the device scope. Reset is handled in V2 from then on, so the old global value never comes back. The original global value stays for other profiles and for rollback. A failed save follows the settings store's existing policy, which distinguishes "applied locally" from "save failed" notices.

The device scope is a kind (mac/windows/linux/mobile), not an individual machine. Values edited by a V1 client are not merged automatically into a profile that has started V2 editing. Server-revision-based merging when several tabs edit the same bindings JSON at once is not implemented; the settings save contract for a single settings key applies.

The Mac Option key setting under Settings → Keymap (키맵) chooses between `Type characters (문자 입력)` and `Terminal Meta key (터미널 Meta 키)`. The default is the Terminal Meta key, and an already-saved choice is kept when the default changes. With character input, Option+P types `π` as a character, so Meta combos of terminal apps stop working. The choice is applied to `macOptionIsMeta` on newly created and already-open terminals. A PWA is not guaranteed to intercept keys that the OS or browser reserves.

## Scopes and virtual input

`core/keyboard-scopes.js` provides push/dispose, top-most ownership, a Tab trap and focus return. The shared panel/dialog, the session sheet, the palette, the worktree/repository dialogs, the attached screen, the search box and keymap recording use it. Enter/Space on ordinary buttons and events inside widgets are left alone. Keys heading for the terminal behind a modal are blocked.

Key bar buttons go through the same keyboard-input adapter and use the shared keyseq modifier rules. No sticky is added to the fixed `Ctrl+C`, tmux prefix and Shift+Tab buttons. Sticky applies only to single-ASCII soft input before encoding. It is neither applied to nor consumes the once-state for physical keydown, IME commits, multi-character input, paste or protocol replies. For browser events whose input source can't be determined, the original text is preserved.

`term/ws.js` does not transform onData bytes. The touch key bar DOM and touch gestures load from the fixed-name `touch.js` chunk. State and encoder exist once, in core. Touch-only code is not part of the desktop initial bundle, and the two Vite builds and the fixed outputs of app.js/app.css/voice.js are unchanged.

## Paste

Text goes through the existing `sendPaste`, and the server decides bracketed paste. The wrapper capture consumes the paste so xterm's separate paste listener can't send it twice. An empty paste also cancels a pending reservation.

Native combos wait for the browser event first. Mac Cmd+V / Shift+Cmd+V and Ctrl+Shift+V, and non-Mac Ctrl+Shift+V, work whether or not a custom action is registered. Non-Mac Ctrl+V keeps quoted-insert. If no event arrives, the Clipboard API is used after 250ms. Only one of native and fallback holds the right to deliver; a native event arriving during a read cancels the fallback. Focus/connection changes, modals/recording and session removal block a reserved delivery.

If a native event arrives late, within 1 second after the fallback completes, it is not sent again and a "run it again" notice is shown. The next paste keydown starts a new transaction. A web paste event has no shared transaction id, so a separate menu paste in this window may also be blocked conservatively. A native event that arrives very late, after 1 second, is not guaranteed to be deduplicated. Normal repeated pastes are never removed on the basis of equal content.

## Verification and remaining scope

Automated checks cover physical `code` matching, single execution, recording/top-most scope, IME/AltGraph/repeat, V2 migration/reset, delivery target, CSI u, native/fallback/empty paste and the key bar. In an isolated Chrome page with the real vendor xterm we confirmed the Ctrl+C bytes, one App + terminal CSI u, the native fallback with a delayed event, and the sticky separation between physical, soft and reply input.

Synthetic Option events and Playwright keys are not proof of the Option characters macOS really emits or of OS-reserved shortcuts. Physical keys on a real MacBook, Safari and Codex/Claude combos inside tmux remain to be measured. For real-device Android verification see below. The tmux extended-keys option is not changed. Protocols are not guessed from product names, and keys not supported today follow xterm's legacy delivery limits.

### Android ADB measurements

Confirmed on a real Android 14 device with Chrome and the Samsung keyboard. The app settings screen was checked on a real running instance; character and modifier checks ran in a separate local page with an isolated raw PTY, so no test keys were sent to a working tmux session.

- 16 keymap rows and 15 delivery-target selects rendered with no horizontal overflow. A native paste row hides its delivery-target select.
- The keymap select uses the shared `vt-select`/`vt-select-wrap` theme. Even in landscape (832×331 CSS px) the coarse-pointer touch target is at least 44px.
- With the Samsung keyboard's `key=Unidentified`, `keyCode=229`, `insertText`, sticky Ctrl+C must not send both `03` and the raw `63` (xterm's delayed textarea check and the custom input could each send one). The beforeinput value of the input to convert is recorded, and the native textarea change that was consumed is reverted so the delayed check can't read the same character again. Without a trustworthy beforeinput value, the original text is preserved and the sticky is not consumed.
- With real ADB touches we confirmed Ctrl+C=`03` once, Alt+C=`1b 63` once and plain c=`63` once. A once modifier turns off after that input.
- The Korean `가 ` composition was delivered once as the original text and did not consume an armed Ctrl.
- With a real ADB Escape, only the rebind recording was cancelled and the settings screen stayed.
- With Android KEYCODE_PASTE we confirmed the paste path once and zero ordinary key bytes. The original ClipboardItem was restored after using a test clipboard.

ADB verifies Android behavior; it does not replace verification of the physical macOS Command/Option keys.
