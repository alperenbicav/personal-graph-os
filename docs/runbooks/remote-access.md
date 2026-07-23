# Remote access runbook

Personal Graph OS's API/UI process (`uv run python -m personal_graph_os.api`) binds to
`127.0.0.1` only and is never meant to accept connections directly from another host. Reaching
it from another device requires a **same-host HTTPS secure proxy**: a reverse proxy that runs
on the same machine as the API process, terminates TLS, and forwards to the loopback backend.
This document describes what that proxy must do and must never do. It is provider-neutral —
plug in whichever reverse proxy you already trust (nginx, Caddy, a managed tunnel, your
platform's own ingress) — and stops short of a copy-pasteable vendor command, because the
exact syntax depends on your proxy and was not verified against a live deployment here.

## Supported vs. unsupported

**Unsupported, unconditionally:**

- Plain HTTP to or from the proxy. TLS terminates at the proxy; the proxy-to-backend hop stays
  on loopback, which is trusted by construction (same host, same kernel).
- Port forwarding or any direct exposure of the backend's loopback port to a LAN or the public
  internet. The backend has no host allowlist entry for anything but `127.0.0.1`/`localhost`
  unless you change `PGOS_TRUSTED_HOSTS`, and changing it to accept a public hostname is exactly
  what step 2 below configures — never bind the backend itself to a non-loopback address.
- Copying the bearer token into a URL, query string, or the frontend build. The token is
  entered once through the unlock screen and lives only in memory plus tab-scoped
  `sessionStorage` (ST-08.1); nothing in this runbook changes that.

**Supported:** the proxy in front of the loopback backend, described below.

## 1. DNS and host allowlist

1. Point your chosen hostname's DNS record at this machine (however your proxy/tunnel
   provider requires — a public A/AAAA record, or a private tunnel hostname).
2. Set `PGOS_TRUSTED_HOSTS` to exactly the hostname(s) the proxy will forward with, e.g.:
   ```
   PGOS_TRUSTED_HOSTS=graph.example.internal
   ```
   The backend rejects any request whose `Host` header isn't in this list (`400`), regardless
   of what the proxy does — this is the last line of defense if the proxy is misconfigured.
   Do not include a wildcard; list every hostname you intend to use.
3. Leave `PGOS_DEV_CORS` unset. A same-origin proxied deployment needs no CORS: the browser
   only ever talks to the proxy's own origin.

## 2. Certificates

- Certificate ownership and renewal belong to your proxy or platform, not to this backend —
  the backend never terminates TLS itself. Use whatever certificate mechanism your proxy
  supports (ACME/Let's Encrypt, a managed platform certificate, an internal CA) and keep its
  private key readable only by the proxy process.
- Rotate certificates on your proxy's normal schedule; this has no interaction with the bearer
  token or `pgos-token rotate` below.

## 3. Proxy request handling

Configure the proxy to, for every request to the chosen hostname:

- Forward to `http://127.0.0.1:<PGOS_PORT or 8000>` unmodified.
- Pass the `Authorization` header through byte-for-byte. **Never log it** — configure the
  proxy's access log format to omit or redact this header explicitly; most reverse proxies log
  headers by default unless told otherwise.
- Preserve the original request `Host`/origin as the backend sees it (so `PGOS_TRUSTED_HOSTS`
  above matches what the backend actually receives) — do not rewrite the Host header to an
  internal name.
- Support streaming/long-lived connections for `POST/GET/DELETE /mcp` (Streamable HTTP): the
  proxy must not buffer the full response before forwarding, and must not impose a read/idle
  timeout shorter than an MCP client's tool-call cadence. Buffering or a short timeout here
  breaks MCP sessions without any error visible in this backend's own logs.
- Raise (or disable) request body size limits and timeouts for attachment upload/download and
  `/export` — these can be large, single-shot binary transfers; a proxy default tuned for small
  JSON APIs will truncate or time out both routes.
- Do not cache proxy-side. `/app/` responses already carry the correct `Cache-Control` from the
  backend (immutable for hashed assets, `no-cache` for the shell/manifest/worker, `no-store` for
  everything else); a proxy cache layered on top can reintroduce exactly the stale-shell problem
  ST-08.2's update-prompt flow exists to avoid.

## 4. Startup

1. Build the frontend once (`npm run build` under `frontend/`) so `frontend/dist` exists.
2. Start the backend: `PGOS_TRUSTED_HOSTS=<hostname> uv run python -m personal_graph_os.api`
   (add `PGOS_PORT` if not using the default `8000`, and `PGOS_DB_PATH` for a non-default
   workspace location). Confirm the startup log shows the loopback bind and that a production
   build was found; it will not print the bearer token.
3. Start/enable your proxy in front of it, pointing at the same port.
4. Retrieve the token once: `uv run pgos-token show` (add `PGOS_DB_PATH=<same path as step 2>`
   if you used a non-default workspace). Enter it in the unlock screen at
   `https://<hostname>/app/` from the remote device. It stays unlocked in that browser tab
   until the tab closes or the token is rejected (e.g. after a rotation).

## 5. Rotation and revocation

Rotation is stop/rotate/restart — a live process holds its token in memory for its whole
lifetime, so replacing the file alone has no effect on it:

1. Stop the backend process.
2. `uv run pgos-token rotate` (with the matching `PGOS_DB_PATH` if applicable). This atomically
   replaces the token file (mode `0600`) and prints the new token once.
3. Restart the backend. Every previously-unlocked browser tab now gets `401` on its next
   request and is dropped back to the unlock screen (ST-08.1); re-enter the new token there.

Rotate immediately if a token may have been exposed (e.g. shared over an insecure channel, or
visible in a screen share). There is no separate "revoke" operation beyond rotation — rotating
invalidates the old token for every client at once.

## 6. Backup, update, rollback

- Backup/restore is unrelated to remote access and already covered by `pgos-backup` (ST-07);
  this runbook adds nothing to that beyond noting that a proxy config file (if any) should be
  backed up alongside the workspace if you want to reproduce the deployment.
- Updating the backend or frontend build: stop the backend, deploy the new code and/or rebuild
  the frontend, restart. The service worker's own update-ready prompt (ST-08.2) handles already-
  open browser tabs; it does not require restarting the proxy.
- Rollback: redeploy the previous backend/frontend version and restart, exactly as in a normal
  (non-remote) rollback. Disabling remote access entirely means stopping/removing the proxy and
  reverting `PGOS_TRUSTED_HOSTS` to the loopback default; the backend and workspace data are
  unaffected either way.

## 7. Incident checks

If you suspect the bearer token or the proxy has been compromised:

1. Rotate the token immediately (§5) — this invalidates every existing session at once.
2. Check the proxy's access log for the suspected window, specifically for requests to
   sensitive paths (`/export`, `/attachments/*/download`, `/mcp`) from unexpected source IPs —
   the backend itself does not log source IPs or request bodies.
3. Check the backend's own log for `401`/`400` spikes (rejected tokens or rejected hosts) around
   the suspected window; a spike suggests probing, not a successful compromise, since a
   successful compromise would show as normal `2xx` traffic under the correct token.
4. If the proxy configuration itself may have been altered, restore it from your backed-up copy
   (§6) and restart the proxy before restarting the backend.
5. There is no remote kill switch beyond stopping the backend process or the proxy; either one
   makes the deployment immediately unreachable.
