# Security policy

[한국어](./SECURITY.ko.md)

FarShell puts a shell on the network, so security reports are taken seriously.

## Reporting a vulnerability

**Please do not open a public issue for a vulnerability.** Use GitHub's private reporting instead:
this repository's **Security** tab → **Report a vulnerability**. Include the version (`cat VERSION`), what you
did, what happened, and what you expected. A proof of concept against your own machine is welcome; please do not
test against anyone else's server.

You will get an answer as soon as the maintainer can, usually within a few days. This is a one-person project:
there is no bug bounty and no guaranteed fix date, but a confirmed report is fixed and credited (unless you prefer
not to be named) in `CHANGELOG.md`.

## Supported versions

The latest release (see [`CHANGELOG.md`](./CHANGELOG.md)) and the `dev` branch. Older lines are not patched —
update to the latest release.

## What FarShell is built to protect, and what it is not

FarShell gives whoever logs in the same power as someone sitting at your Mac's terminal. It is built to keep
**everyone else** out and to limit what a stolen phone or a leaked link can do:

- Remote access sits behind a password (scrypt hash), an optional second factor for new devices (`fsh otp setup`),
  per-device revocation (`fsh device revoke`), lockouts per visitor, and a Host/Origin check against DNS-rebinding
  and cross-site requests. `fsh start` does not start a server without a password (it creates one when none is set).
- The public entrance is a Cloudflare quick tunnel by default. Anyone who learns its URL reaches the login page;
  `VT_TUNNEL_ALLOWED_MAIL` puts an email PIN in front of that, and `fsh start --network tailscale` keeps the
  server off the public internet entirely.
- The code viewer is read-only and confined to configured folders with a deny list; share links are signed,
  expiring and revocable; exposed ports have their own access modes.

It is **not** built to defend against:

- **Another process running as your macOS user.** It can read `~/.vt`, `~/.vt.env` and your tmux sessions. Agents
  you start run as you. The permission engine records and limits what agents do through FarShell, but it cannot
  stop a program that already has your account (`docs/ref/auth.md`, "same-uid").
- Someone who can already read your disk, or a compromised browser on a device you registered.
- A Cloudflare quick tunnel being seen by Cloudflare: TLS ends at their edge. `--e2e` adds an encrypted layer on
  top for the terminal stream.

## Hardening checklist

1. Set a strong password (`fsh password`) and turn on `fsh otp setup`.
2. Prefer Tailscale (`fsh start --network tailscale`) when you do not need the public internet.
3. If you do use the public tunnel, set `VT_TUNNEL_ALLOWED_MAIL` and use `fsh device list` / `fsh device revoke`
   to prune devices you no longer own.
4. Keep `VT_BROWSE_ROOTS` as narrow as you can; the default is `~/GitHub`.
5. Run `fsh doctor` after changes and update to the latest release for security fixes.

## Verifying a download

Release assets are attached to the GitHub release together with a `.sha256` file:

```bash
shasum -a 256 -c farshell-X.Y.Z.tar.gz.sha256
gh attestation verify farshell-X.Y.Z.tar.gz --repo <owner>/farshell   # build provenance, when present
```

The attestation is produced by the release workflow; releases published before it was added do not have one.
`install.sh` clones the `master` branch, which only ever holds released code.
