# ADR-29 — the rail's subject is the session again

> Split out of [`terminal.md`](./terminal.md) on 2026-09-19: the rollout had grown into a
> single 13,895-character table cell, which is longer than every other row in that table
> combined. Read [`terminal.md`](./terminal.md) first for the area; come here before you
> touch the rail, the tab strip, grouping, or the sleep/wake path.
>
> **Sections 1–2 are current. Sections 3–4 are history** — a lot of what steps A–E built
> was deleted again by addendum #4. Don't take a mechanism from them without checking
> §1 first.

---

## 1. What is true today

**A group is derived from the screen, not stored.** A tab whose pane tree holds 2+
distinct sessions *is* a group; a tab with one session is an individual. Nothing persists
group membership — no tag, no file, no route.

- **Making a group**: drag a session onto the terminal area to split it, reusing
  `layout/dnd.js`'s existing split drop zones. Rail rows are `SESSION_MIME` drag sources
  for this (`RailRow.tsx` / `Rail.tsx`). There is no other way, by design.
- **Naming a group**: the tab's own `label` (`window.vtRenameTab`). When unset,
  `tabDisplayLabel()` (`layout/store.js`) computes a label from the member names.
  `tabGroups()` in the same module is the live group list, exposed as `window.getTabGroups`
  because `Rail.tsx` cannot statically import state modules (ADR-26 chunk isolation).
- **A tab has no identity beyond its own id.** The `groupId` field on a tab is gone.
- **Closing a pane** (`layout/panes.js`) sleeps the session it held *only if the tab was a
  group at that moment*. Dragging a pane elsewhere (the pane-replace flow) leaves both
  sessions awake as individuals instead.
- **Rail rows are sessions** (`shell/Rail.tsx`, local host only), each either **awake**
  (has a web session in *this browser's* `allSessions()`) or **asleep** (tmux alive, no web
  session here — distinct from a full kill, and a separate action). Awake rows sort
  alphabetically, not by recency. Asleep rows live in a separate "잠자는 중" zone,
  collapsed by default, and are always solo (see §5).
- **Remote hosts are untouched** (2.2 range). They keep the old three-bucket
  개입 필요 / 작업 중 / 열려 있지 않음 view via `buildRailSections()`, branched on
  `isRemoteHost()`.
- **Deleted server-side**: `group_store.py`, `routes/groups.py`, and all `@fsh_grp`
  handling in `tmux_runner.py` / `routes/tmux.py`. `~/.vt/groups.json` is no longer
  written. `@fsh_wt` (worktree tagging, D2) is unrelated and still live.

### Sleep / wake vocabulary

Three distinct actions, named in 2.1 D3 and implemented in `term/session-actions.js`:

| Action | What it does |
|---|---|
| 재우기 (sleep) | Detach the web session; tmux keeps running. `vtDetachSession` for one session, `sleepTab()` for a whole tab |
| 깨우기 (wake) | Re-attach an asleep tmux session |
| 완전 종료 (kill) | `vtConfirmAndKillSession` — kills the tmux session. The confirmation names the session's agent and warns when it is `waiting`/`working`, so a session mid-approval isn't killed by mistake |

The tab **×** is 재우기, not "remove from layout": closing a tab closes the whole group.
`sleepTab()` walks the tab's pane tree, sleeps every session leaf, then closes the tab.
(`removeFromLayout` — layout-only, no detach — was removed; its single caller needed the
sleep behaviour.)

### Layout snapshot

`v4`: `{tabs:[{id, worktreeId, hostId, label, tree}], activeTab}`. `layout/persist.js` no
longer writes the `repoId` mirror that existed as a v3 rollback shim, and `repoId` /
`groupId` on old `v3`/`v4` snapshots are **dropped on load** — they no longer mean
anything. Everything else about an old tab (id, `tree`, `worktreeId`, `label`) restores
normally, and `v1` (one tab) / `v2` (no group) are still read. Old snapshots carrying the
literal placeholder label `작업 공간` are treated as unset on read, because that string was
once `_makeTab`'s default and `tabDisplayLabel()` would otherwise mistake it for a name the
user chose.

---

## 2. Why ADR-29 reversed ADR-20

**ADR-29 supersedes ADR-20** (`docs/archive/v2.1/plan/00-decisions.md` — local only, gitignored).
The subject is back to the session, with the repo/worktree-tab model demoted to a display
convenience — see the "Header tabs" row in [`terminal.md`](./terminal.md) for the
tab mechanics that survived.

ADR-20's own blocking argument for the worktree model was that rail rows would shift under
`cd`. D2's `@fsh_wt` tagging resolved that, which is what made the reversal possible.

Worktree management moved out of the rail into the **저장소 관리** sheet
(`RepoVisibility.tsx`), which reads `/api/repos` (grouped by repo) rather than the flat
`/api/worktrees`. Each worktree card carries its branch, a **+ 새 세션** button
(`openWorktree()` + `attachTmux`), a **삭제** action when it isn't the main worktree, and
its tmux sessions as clickable chips. The repo tier is flat: most repos have exactly one
(main) worktree, so the hide toggle and the **+ 워크트리** entry point sit on the main
worktree's own card, and non-main cards carry a small repo-name label instead of a
checkbox. The rail's footer button is **새 세션** (the global `session.add-menu` action),
and the `worktreeNew` keymap (Alt+Shift+N) opens this sheet rather than the create-worktree
dialog.

---

## 3. The A–E rollout (2026-09-18, historical)

All five steps landed the same day: A (group storage groundwork) → B (rail row = session)
→ C (repo sheet) → D (tab identity = group, sleep/wake) → E (drag-to-regroup, popup
cleanup). **Steps A and E were then undone by addendum #4** — read them for why the
mechanism existed, not for what the code does now.

### Step A — group storage (deleted in addendum #4)

Group membership went on the tmux session itself as `@fsh_grp`, the same mechanism as
`@fsh_wt`: one more field on the already-batched `list-panes` / `list-sessions` calls, no
new subprocess. A group's chosen name and order lived in `group_store.py` →
`~/.vt/groups.json`, with routes `GET`/`PATCH /api/groups`, `POST /api/groups/reorder` and
`POST /api/tmux/{name}/group`.

### Step B — the rail row becomes a session

Awake sessions rendered through `buildSessionSections()` (`shell/rail-data.ts`): an
**개입 필요** bucket for waiting/error regardless of group, then group buckets (order from
`group_store`, unlabeled groups falling back to the session's `@fsh_wt`→repo name via
`groupDisplayLabel()`), then **묶지 않음** for sessions with neither a group nor a repo.
`buildSleepingEntries()` put asleep sessions in their own collapsed zone, where a group
that slept as a whole rendered as one cluster row (click wakes every member via
`Promise.all(attachTmux)`) and a solo sleeper rendered as a solo row. The session context
menu gained **완전 종료** (previously reachable only from the `tmux-panel.js` popup) and
**깨우기**.

### Step C — the repo sheet

See §2 — this is the part that survived, apart from the collapsible repo tier that
addendum #2 flattened.

### Step D — tab identity, and the regression B introduced

B's rail `openRow` never switched tabs before calling `switchTo`/`attachTmux`, and those
always place a session in **whatever tab is currently active** (`addSession`'s last line).
So opening a session that belonged to a different repo *silently overwrote the pane you
were looking at* instead of switching to that repo's tab. Fixed by calling
`window.openGroupTab()` (renamed from `openWorktreeTab`) from the rail and the repo sheet
first, for any row with a non-null `groupId`; a `⌘`-click split, or a row with no group,
skips it.

Tab identity moved from D4's `repoId` to `groupId` across `layout/store.js`,
`layout/tabbar.js` (`sessionBelongsToTab` / `setGroupSessionMap`) and `term/tab-worktree.js`
(`effectiveGroupMap` — `@fsh_grp` first, repo id fallback, matching the rail's own
computation exactly). The snapshot went to `v4`. The tab **×** became 재우기 (see §1).

**Verified** on a real isolated server + browser: two sessions from different repos create
two separate tabs (previously the second ate the first's pane); the tab's 재우기 sleeps
every session in it while tmux stays alive (`attached: 0` in `/api/tmux/sessions`) and the
rail immediately lists them under 잠자는 중; waking from there reopens the tab.

### Step E — drag-to-regroup (deleted in addendum #4) and two cleanups

Dragging a session row onto a group header called `POST /api/tmux/{name}/group` to set
`@fsh_grp`; dropping on 묶지 않음 cleared it. 개입 필요 and 잠자는 중 were never drop
targets — the former is a filtered view, not a real group, and regrouping only makes sense
while awake.

The cleanups the whole rollout had been blocked on, both permanent:

- **`layout/rail.js` deleted** — the 48px icon rail and its side flyout, already fully
  superseded by the Solid rail and the real Settings panel. Its one still-used
  registration, `clients.show`, moved to `layout/connected-screens.js` (a small
  backdrop + card dialog matching the other sheets).
- **`term/tmux-panel.js`'s session-list popup (`showTmuxSessions`) deleted** — which, on
  investigation, was still used by the *mobile* session sheet (`picker.js`) and a header
  chevron, not just the desktop rail. Removing it meant extending `picker.js` to list
  asleep tmux sessions directly (dimmed, `.sleeping`) with wake and kill buttons.
  `attachTmux` / `createTmuxSession` — the real actions, not the popup — moved to
  `term/session.js` next to `createPlainSession`.

**Verified** on a real isolated server + browser: a simulated HTML5 drag moved a session's
`grp_id` to the target group and the rail showed it merged in; the 연결된 화면 dialog opens
from the rail context menu with no console errors; the mobile sheet lists a genuinely
asleep tmux session, wakes it on click, and fully kills a live one after confirmation.

---

## 4. Post-E addenda (same day, all from user feedback)

### #1 — group rename via `window.prompt` (superseded by #2)

A pencil icon on each real group header (not on 묶지 않음 / 개입 필요) opened
`window.prompt` and called `renameGroup()` → `PATCH /api/groups/{id}`.

### #2 — no auto-suggestion, no native dialogs, flatter repo sheet

1. **The repo auto-suggestion fallback from Step B is removed** (`@fsh_wt`→repo id when
   there is no `@fsh_grp`) — the user never asked for it. A session with no group became
   unconditionally 묶지 않음, full stop (`Rail.tsx`'s `groupId` computation,
   `term/tab-worktree.js`'s `effectiveGroupMap`; the now-dead `repoOwnerMap` helper was
   deleted). That left no group header to drag onto until *something* was already grouped,
   so session rows became drop targets too: dropping A on B joined B's group, or — if
   neither had one — minted a fresh 12-hex id client-side (`newGroupId()` in
   `rail-data.ts`) and put both in it. No server pre-registration was needed, since a group
   "existed" purely by virtue of sessions carrying its id.
2. **The `window.prompt` from #1 is gone** — the pencil swaps the header's toggle+label for
   an inline `<input>` (Enter/blur saves, Escape cancels), per a direct instruction not to
   use native dialogs.
3. **The repo sheet's collapsible repo tier is flattened** — see §2.

**Incident, self-caught and fixed.** This addendum's browser verification used an isolated
server missing `VT_STATE_DIR`, so `group_store.py` wrote two test labels into the real
`~/.vt/groups.json` before it was noticed. Backed up and reset to empty. Isolated servers
need `VT_CONFIG`, `VT_STATE_DIR`, `VT_WORKSPACE_PATH` and `VT_TMUX_SOCKET`, or they write
to the real user's state.

### #3 — the repo-hash color dot, and two dead leftovers

The per-session repo-hash color dot (`.vt-wgrail-hash`, keyed on the git remote's
owner/repo name via `fnv1a` / `hashRepoColorIndex` / `repoColorKey` in `rail-data.ts`) was
the last visible repo-centric leftover the user found. Removed with its full dependency
chain: the `gitRemote` field threaded through `Rail.tsx`'s `wtInfo` map (it existed solely
to feed this color), the `.vt-wgrail-hash` span in `RailRow.tsx`, and the
`--color-hash-1..8` tokens across `tokens.css`, `skins.css` (5 skin blocks) and
`theme-import.js`'s dynamic generation.

The same investigation surfaced two genuinely dead leftovers, removed at the same time:
`remoteLabel` / `RailRemote` (exported and tested, never called by any UI since before
Step B) and `.vt-wgrail-bar.kind-session { background:transparent; }` (dead since Step B
made `RailRow.tsx` always render `tone-${status}`). Rows render with only the status-tone
bar now.

**Verified** on an isolated server (`VT_STATE_DIR` included this time) + real browser: no
color dot, repo sheet and grouping still work, no console errors.

### #4 — group redefined from tag to screen-derived

The implementation didn't match what was asked: *"group = sessions sharing one screen, side
by side or stacked — everything else is individual."* This is the change that produced §1,
and it deleted Step A's storage and Step E's drag-to-regroup outright.

The same pass fixed four smaller things the user reported:

- Rail rows reordering by recency on every 4–5s poll → fixed alphabetical order.
- A real regression: the rail's session context menu had lost **rename**. `tabbar.js`'s own
  comment claimed it had moved there; it hadn't. Restored as an inline edit, no
  `window.prompt`.
- The tab header defaulting to a meaningless `작업 공간` placeholder → always the actual
  member name(s).
- The top tab-bar "new session" button (`#add-btn`), whose popup positioned itself against
  a hardcoded element instead of wherever was clicked → removed in favour of the rail's own
  button, and fixed to position from the real click target.

**Verified** with typecheck, the full test suite (524, down from 540 after removing the
group-tag tests) and a production build.

---

## 5. Known regressions and rough edges

- **Sleeping group clusters are gone.** Step B's deliberate deal — sleep a whole group,
  wake it as one chunk — depended on a stored tag. Once a tab closes there is nothing left
  to remember "these slept together", so sleeping rows are always solo. Re-splitting them
  on wake re-forms the group.
- **Mixed-repo group labels.** `groupDisplayLabel()`'s "first member with a `repoName`"
  fallback was written when a group could only ever contain one repo, so a mixed-repo group
  can be represented by an arbitrary repo name. Naming the group explicitly fixes it.
  Tracked in the local backlog (`docs/TODOS.md`, gitignored).
