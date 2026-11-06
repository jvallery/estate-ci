#!/usr/bin/env python3
"""Mint a GitHub App installation token scoped to ONE repository (stdlib only).

  APP_CLIENT_ID=... APP_PRIVATE_KEY="$(pem)" app-token.py <owner/repo>   -> token on stdout
  APP_TOKEN=... app-token.py --revoke

Used by pin-bump.yml (D-52, vallery-pin-bot) so pin PRs are opened by an App
and start pull_request workflows. The token is limited to the named repository
and to contents + pull_requests write; it expires after one hour and pin-bump
revokes it when the step ends. Never print the output; the caller masks it.
"""
import base64
import json
import os
import sys
import time
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def der_items(buf):
    """Yield (tag, value) for consecutive DER TLVs in buf."""
    i = 0
    while i < len(buf):
        tag, n = buf[i], buf[i + 1]
        i += 2
        if n & 0x80:
            k = n & 0x7F
            n = int.from_bytes(buf[i:i + k], "big")
            i += k
        yield tag, buf[i:i + n]
        i += n


def rsa_key(pem):
    body = "".join(l for l in pem.strip().splitlines() if not l.startswith("-----"))
    seq = next(der_items(base64.b64decode(body)))[1]
    items = list(der_items(seq))
    if items[1][0] == 0x30:  # PKCS#8: version, algorithm, OCTET STRING(PKCS#1)
        items = list(der_items(next(der_items(items[2][1]))[1]))
    n, d = (int.from_bytes(items[i][1], "big") for i in (1, 3))
    return n, d


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def jwt(client_id, pem):
    import hashlib
    n, d = rsa_key(pem)
    now = int(time.time())
    msg = b64u(b'{"alg":"RS256","typ":"JWT"}') + b"." + b64u(
        json.dumps({"iat": now - 60, "exp": now + 540, "iss": client_id}).encode())
    # EMSA-PKCS1-v1_5 with the SHA-256 DigestInfo prefix.
    t = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(msg).digest()
    k = (n.bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    sig = pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")
    return (msg + b"." + b64u(sig)).decode()


def call(method, path, token, body=None):
    req = urllib.request.Request(
        API + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def main():
    if sys.argv[1:] == ["--revoke"]:
        call("DELETE", "/installation/token", os.environ["APP_TOKEN"])
        return
    owner, repo = sys.argv[1].split("/", 1)
    app = jwt(os.environ["APP_CLIENT_ID"], os.environ["APP_PRIVATE_KEY"])
    inst = call("GET", f"/repos/{owner}/{repo}/installation", app)["id"]
    tok = call("POST", f"/app/installations/{inst}/access_tokens", app, {
        "repositories": [repo],
        "permissions": {"contents": "write", "pull_requests": "write"},
    })["token"]
    sys.stdout.write(tok)


if __name__ == "__main__":
    main()
