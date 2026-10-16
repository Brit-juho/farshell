# fsh help permissions — Agent approval inbox and permissions

Some things an agent wants to do on this Mac need a person's approval — reading or typing into a shared session, messaging an agent in
another session, giving another Mac control, or any permission the agent asks for directly. All of these requests land in **one inbox**,
and the permissions you approve are managed in **Settings > Permissions**. Approving and revoking work only in a browser logged in with the
password (web/phone). `fsh authz` in the terminal is read-only.

## When a request arrives

- A decision window opens. It shows **one request at a time**, oldest first (`1 / N`). There is no "allow all".
- The window shows who asks (with the lineage if it is a delegated child session), the target, a sentence that names the request by its
  **preset** (View · Collab · Drive · Control) and the exact scopes and conditions verbatim. If the agent wrote a reason, it is shown separately.
- The choices are **Deny / Once / For this session**. Message and other-Mac requests cannot be allowed once, and the window says why.
- Against slips: focus starts on "Deny", no button reacts for a moment after the window opens, and Esc, the backdrop or X mean "Later", not a decision.
- A request that includes **elevated access** (raw input, browser JS, ...) is marked clearly, and allowing it needs your password again.
- Notifications never contain session names or request content.
- A request closed with "Later" stays. Reopen it from the count badge on the Settings button, or from Settings > Permissions > Waiting for approval.

## Settings > Permissions

| Section | What it shows |
|---|---|
| Waiting for approval | Every pending request in one list. "Decide" opens the decision window for that request |
| Pairs | Pairs in progress and those that ended within the last hour: state, members with roles, bundle, time left. End/revoke (confirmed), offer a role swap or accept one; the side that started a pair gets "Show code" (the code, its check phrase and a live countdown). A server with the feature off shows one explanatory line |
| Folder leases | Leases held now: holder, path, read/write, exclusive, expiry. A person releases one with "Release" (confirmed). An inherited lease is a read-only marker and goes away when its parent is released. A lease is an advisory marker that cannot stop writes made outside FarShell itself (shell, editor) |
| Granted access | A list you can filter by subject, resource and kind. Each row: preset (or scope count), the exact scopes and conditions, who approved and how, expiry or uses left, delegation lineage. "Revoke" asks for confirmation |
| Built-in rules | The defaults set in code. Read-only; they cannot be revoked |
| Audit log | Newest first. Filter by principal, resource and time, and choose whether denials are included. A healthy hash chain shows a plain "integrity verified"; a broken one shows a warning. Content (input, screens, messages) is neither recorded nor shown |
| Emergency stop | Revokes every permission held by non-human principals and clears pending requests, with two-step confirmation and a fresh password check. Your login and your own permissions stay |

For a shared session you can also use "Revoke input only" to keep reading and take back writing.

## Blocked queue items

When a queue item is blocked because a permission was revoked or a delegation closed, the queue panel shows the reason in words (the code stays in the API).

## Folder leases — «this path is mine for now»

So that two agents do not edit the same folder at once, an agent (MCP `lease_acquire`) or you in a terminal (`fsh lease acquire <path> --write`)
takes a **lease** on a folder. Default 30 minutes, at most 8 hours (extend with `lease_renew` / `fsh lease renew`). If another session tries to
take a write lease on an overlapping path it is refused, with the holder's nickname and when the lease ends. Holding a lease needs the write
(or read) permission on that path — an agent that lacks it asks with `request_access`. A delegated child in the same folder shares the parent's
lease; a child started in a worktree gets its own lease on the new worktree.

- **Enforced**: only when the server runs with `lease` in `VT_AUTHZ_ENFORCE`, the writes FarShell performs itself (git stage/commit from the web, files sent to a session, a delegation's folder) are refused on someone else's leased path.
- **Advisory**: what an agent does with its own tools, and any other program of the same user account. **A lease is a marker, not a lock** — a process of the same account can ignore it.
- People are never blocked. Writing inside someone else's lease only leaves a record and a notice.
- **Commit guard (optional)**: `fsh lease guard install` stops a commit that includes files under someone else's lease and tells you who holds it. If the server cannot be reached it only warns and the commit goes through. An existing pre-commit hook is kept and still runs first.
- A person releases a lease in Settings > Permissions > Folder leases (API `DELETE /api/authz/leases/{id}`). Revoking a permission also ends the leases taken with it.

## pair — two sessions on one job

Two agents (or one person and one agent) split the same bundle of sessions and folders into **driver** (types and writes) and **navigator**
(reads and messages). It is on only if the server's `VT_AUTHZ_ENFORCE` contains `pair` (otherwise `fsh pair` says it is off).

1. One side runs `fsh pair start --session NAME --dir FOLDER` (or MCP `pair_create`) and gets a code (`ABCD-EFGH`) and a **two-word check phrase**. The code is single-use, valid 5 minutes, and burned after 5 wrong tries.
2. The other side runs `fsh pair join <code>` (or `pair_join`). It learns the result **as itself** with `fsh pair status`, not through the code.
3. The inbox shows one «Pair» request: who is pairing with whom, the whole bundle, what each role gets (the preset name and exact scopes) and **both check phrases**, **on one screen**. Compare the phrases one by one with what each agent displayed on its own screen (deny if they differ). The allow button spells out what it grants.
   **High-risk** items (raw input, session management, relay) each have a checkbox (all off at first) and only ticked ones are given; ticking any asks for your password once more when you allow. Agents cannot approve. Widening a pair later is a new approval.
4. There is exactly one driver. `fsh pair handoff <id>` offers, the other side's `fsh pair accept <id>` swaps (an offer lapses after 2 minutes). A person can always type.
5. It ends on `fsh pair end <id>`, a person revoking it in the permissions screen, 2 hours idle (`VT_PAIR_IDLE_MIN`), a member session disappearing, or 8 hours. Then the permissions, leases and queued messages between the members are cleaned up (`fsh pair revoke <id> --verify` confirms).

A program of the same user account can edit the permission store directly — a pair does not stop that; it makes clear who may do what and cuts accidents short.

## Browser - isolated tabs for agents

Agents can open **isolated browser tabs** (a fresh profile - your own Chrome and its logins are never touched) to see and drive the localhost ports FarShell has exposed.
It is on only if the server's `VT_AUTHZ_ENFORCE` contains `browser` (otherwise `fsh browser` says it is off).

- **Turn it on**: Settings > Permissions > Browser, or `fsh browser enable NAME` - the first time you confirm a consent text. Agents cannot turn it on. An unusable adapter shows why.
- By default only `about:blank` and **exposed localhost ports** open. Anything else must be in the allowlist (`url_allowlist`) you give when approving; private-network and metadata addresses need the exact IP written down, and `file:`, `chrome:`, `data:` never open. Every navigation, redirect, iframe and the address opened after a click is checked again, and a violation puts the tab back on a blank page.
- **Running scripts (`browser.js`)** and **cookie, storage and network values (`browser.sensitive`)** are given to nobody by default - they need your approval with your password re-entered. Cookie/storage **values** need an extra «values» condition on top; cookie/auth headers and bodies over 64 KB in the network view are always hidden.
- A tab belongs to the agent that opened it. Another agent needs a permission that names that tab (or the same pair). Settings and `fsh browser tabs` / `close <id>` list and close every tab.
- If the adapter goes down the tab becomes `orphaned` (still owned, but not operable); revoking a permission **does not close** a tab, it only releases ownership. A tab an agent hands over with `browser_handoff` is shown as taken over by you.
- Connecting to your own Chrome (profile=user) and downloads are not supported yet.

## Agents on another Mac - reading and typing into shared sessions, remote delegation

An agent on one Mac can read and type one line into a session that is **shared** on another Mac, or have that Mac create a sub-session (this is not the same as giving it a `control`
shell). Both Macs must be paired (`fsh host`) and both servers need `peer` in `VT_AUTHZ_ENFORCE` (`deleg` too for delegation). Once on, the MCP tools `list_shared_sessions`,
`read_session`, `send_to_session`, `spawn_session` and `list_delegations` take a `host` argument (a host id).

- The first use puts a request in **the other Mac's** inbox. The agent asked; the person on that Mac approves (nobody approves on the asking side). Once approved, repeat the same call.
- **Reading** needs a normal approval; **typing and remote sub-sessions** can only be approved by a person who re-confirmed their password. A read-only share cannot be typed into.
- The agent only sees **shared** sessions on the other Mac (no session names or folders). A remote sub-session starts in that Mac's default folder; a working folder or worktree cannot be chosen.
- If you revoke on the other Mac or remove the Mac (`fsh host rm`), open remote terminal screens close within 5 seconds.
- `fsh host rotate <id>` replaces the secret of the connection going out to that Mac on both sides (no expiry). If it fails midway the previous secret stays. Rotate the opposite direction from the other Mac with the same command.

See also: `fsh help agent-state` (MCP tools `request_access` and `whoami`), `docs/ref/auth.md`.
