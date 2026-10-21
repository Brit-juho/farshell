# MCP, skill and plugin management

Open **MCP · Skills** in the dock and switch between **All FarShell** and **Selected session**.

- **All FarShell:** collects default user settings, registered projects, native CLI project records and profiles verified from running sessions. Verified profile roots remain available later. It does not recursively search your home or move files. Large inventories load progressively in pages of 32 settings contexts.
- **Selected session:** uses the actual settings root and project of a running Claude or Codex process. Unidentified profiles are never replaced with guessed defaults.
- Filter by type, tool, scope, category or search. Expand an item for its source file, profile, scope, origin and notes. User/profile changes affect other sessions; shared project files may affect your team.

## Organization and presets

Categories describe usage; presets save enabled/disabled combinations. Read-only items can also be selected for categorization. Mark origins as personally authored, Git-sourced, marketplace-installed, locally installed or unknown.

Select supported MCP servers, skills and plugins to enable or disable them together. Saving a preset records the selected items' current enabled/disabled states. Review target files and profiles before applying. Items outside the preset remain unchanged. Changed files or sessions and missing items require another review. Multi-file changes are not globally atomic; failures are reported per item.

Plugin-provided skills and MCP servers appear under their parent. Use the owning CLI's parent-plugin commands for installation, updates and removal. Claude manages activation of directly installed skills; Codex uses native disable settings. Linked skills remain visible but are not edited through links.

Edit source supports directly installed skills and supported MCP definitions. Enter only changed MCP fields as JSON. Omitted fields and existing secrets are preserved; existing arguments, URLs and credential values are never returned to the editor. Codex edits only safely identifiable TOML assignments and preserves unrelated settings. Unsupported formats remain unchanged. Existing library, import and deployment tools remain under **Existing MCP library and advanced settings**.

## Environment and application

Environment overlays for a selected local session apply in this order: **machine-wide → profile → project**. They affect FarShell's restarted CLI, not existing shell files or running processes. Values are stored as plaintext in a private 0600 state file; the UI returns names and presence only. Login and OAuth files are not copied. Execution paths, loader variables and security boundary variables cannot be edited here.

**Restart CLI when safe** verifies original arguments and the conversation ID, then waits during work, approvals, recent typing or unsent input. After a normal exit, it resumes the same profile, directory and conversation. Unsupported wrappers, temporary profiles, unknown arguments and unrecognized input screens are not stopped automatically. Waiting jobs can be cancelled and expire after 15 minutes. Server restarts do not automatically restore waiting jobs.

Verifying a resumed CLI does not establish MCP connection success; actual MCP runtime state remains unknown. This does not restart the FarShell server or tunnel. Concurrent input from an external program directly attached to the same terminal is not controlled atomically.

## Updates, synchronization and remote approval

Update checks require verifiable provenance. Dedicated GitHub skill checkouts compare their commit with the public remote branch, and report local edits separately. Claude plugins compare installed versions with the local marketplace catalog, which does not establish remote freshness. Unverifiable items are not labeled current. Nothing is downloaded or overwritten automatically.

Synchronization shows the existing peer-sync installation, active scheduler, lock record, MCP conflict-record count and last log time. It does not return log content or secrets. Only the existing runner's active scheduler can be triggered; no second sync engine is introduced. Categories, presets, environment overlays and restart records belong to their FarShell host. The existing peer-sync scope is not expanded automatically.

Remote writes become requests. **A human directly logged into the target host must review and approve them** under Remote approvals. Revoked host permission or expired plans prevent application. Operations without direct remote support, including environment and category editing, are managed on their owner host. Both hosts need this FarShell feature.
