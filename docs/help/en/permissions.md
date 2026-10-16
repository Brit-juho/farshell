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
- There is no screen yet for a person to revoke a lease; use the API (`DELETE /api/authz/leases/{id}`). Revoking a permission also ends the leases taken with it.

See also: `fsh help agent-state` (MCP tools `request_access` and `whoami`), `docs/ref/auth.md`.
