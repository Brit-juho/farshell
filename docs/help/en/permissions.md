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

See also: `fsh help agent-state` (MCP tools `request_access` and `whoami`), `docs/ref/auth.md`.
