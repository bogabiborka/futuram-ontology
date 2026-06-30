"""OAuth device-flow login for providers whose tokens are obtained interactively
rather than pasted from a dashboard — chiefly GitHub Copilot.

The device flow (RFC 8628) suits a CLI/headless bench: no redirect server, no
client secret. We ask GitHub for a device + user code, the user opens a URL and
types the short code, and we poll until GitHub returns an OAuth token. That token
is cached under the user's config dir and (for Copilot) exchanged for the short-
lived Copilot API token at run time.

Tokens are cached to ~/.config/futuram-bench/<provider>.json with 0600 perms and
are NEVER written to the repo, transcripts, results, or providers.json.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

# The well-known public client_id of the GitHub Copilot / VS Code OAuth app.
# (Public identifier, not a secret — the device flow needs no client secret.)
GITHUB_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
_GH_DEVICE_CODE_URL = "https://github.com/login/device/code"
_GH_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"


def _cache_dir() -> Path:
    base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / "futuram-bench"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_token_path(provider: str) -> Path:
    return _cache_dir() / f"{provider}.json"


def load_cached_token(provider: str) -> str | None:
    """Return a previously logged-in OAuth token for `provider`, or None."""
    p = cached_token_path(provider)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("access_token") or None
    except Exception:  # noqa: BLE001
        return None


def _save_token(provider: str, token: str) -> Path:
    p = cached_token_path(provider)
    p.write_text(json.dumps({"access_token": token}))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def _post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "futuram-bench",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def github_device_login(client_id: str = GITHUB_COPILOT_CLIENT_ID,
                        scope: str = "read:user",
                        prompt=print) -> str:
    """Run GitHub's OAuth device flow interactively and return the OAuth token.

    `prompt` is the sink for user-facing instructions (defaults to print, so it
    works from a CLI; the observer can pass its own to surface the code in the
    UI). Blocks until the user authorises or the code expires.
    """
    start = _post_form(_GH_DEVICE_CODE_URL,
                       {"client_id": client_id, "scope": scope})
    device_code = start["device_code"]
    user_code = start["user_code"]
    verify_url = start["verification_uri"]
    interval = int(start.get("interval", 5))
    expires_in = int(start.get("expires_in", 900))

    prompt("\n  GitHub login required.")
    prompt(f"  1. Open: {verify_url}")
    prompt(f"  2. Enter code: {user_code}")
    prompt("  Waiting for authorisation…")

    deadline = time.monotonic() + expires_in
    while time.monotonic() < deadline:
        time.sleep(interval)
        resp = _post_form(_GH_ACCESS_TOKEN_URL, {
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        if resp.get("access_token"):
            return resp["access_token"]
        err = resp.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += int(resp.get("interval", 5))
            continue
        if err in ("expired_token", "access_denied", "unsupported_grant_type",
                   "incorrect_client_credentials", "incorrect_device_code"):
            raise SystemExit(f"GitHub device login failed: {err} "
                             f"({resp.get('error_description', '')})")
        # unknown transient error: keep polling until the deadline
    raise SystemExit("GitHub device login timed out before authorisation.")


def login(provider: str, prompt=print) -> str:
    """Interactively obtain and cache an OAuth token for `provider`. Returns the
    token. Currently supports 'copilot' / 'github' (GitHub device flow)."""
    provider = provider.lower()
    if provider in ("copilot", "github"):
        token = github_device_login(prompt=prompt)
        path = _save_token("copilot", token)
        prompt(f"  ✓ logged in — token cached to {path}")
        return token
    raise SystemExit(f"No OAuth login flow for provider {provider!r} "
                     f"(supported: copilot/github).")
