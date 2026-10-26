"""Register the dev App through GitHub's manifest confirmation flow.

Creates an App and repository configuration only after the browser confirmation.
Private key material is captured locally and sent to `gh secret set` on stdin.
"""

import argparse
import html
import json
import os
import re
import secrets
import subprocess
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]


def save_and_configure(app, repo, directory):
    app_id, slug, pem = app["id"], app["slug"], app["pem"]
    if not isinstance(app_id, int) or app_id <= 0 or not re.fullmatch(r"[a-zA-Z0-9-]+", slug):
        raise ValueError("Invalid App registration metadata")
    if not pem.startswith("-----BEGIN ") or "PRIVATE KEY-----" not in pem:
        raise ValueError("Missing App signing key")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("The local secrets directory must not be a symlink")
    key_path = directory / f"infra-app-{app_id}.private-key.pem"
    descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(pem)
    subprocess.run(
        ["gh", "secret", "set", "INFRA_APP_PRIVATE_KEY", "--repo", repo],
        input=pem,
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["gh", "variable", "set", "INFRA_APP_ID", "--repo", repo, "--body", str(app_id)],
        capture_output=True,
        check=True,
    )
    metadata = {
        "app_id": app_id,
        "slug": slug,
        "repo": repo,
        "key_path": str(key_path),
        "install_url": f"https://github.com/apps/{slug}/installations/new",
    }
    (directory / "infra-app-registration.json").write_text(json.dumps(metadata, indent=2))
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="AlexShen12/bandwidthinfra")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--name", default="bandwidth-infra-reviewer-dev")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("--repo must be OWNER/REPO")
    state = secrets.token_urlsafe(32)
    manifest = json.loads((ROOT / ".github/infra-reviewer/app-manifest.json").read_text())
    manifest.update(name=args.name, redirect_url=f"http://127.0.0.1:{args.port}/callback")
    completed = {"done": False, "ok": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Callback query strings contain one-time credentials.

        def page(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == f"/start/{state}":
                action = f"https://github.com/settings/apps/new?state={state}"
                self.page(
                    200,
                    f'<h1>Register InfrastructureAgent</h1><p>Creates the dev App and stores its signing key as a repository secret for {html.escape(args.repo)}. Bedrock credentials are configured separately.</p><form method="post" action="{action}"><input type="hidden" name="manifest" value="{html.escape(json.dumps(manifest), quote=True)}"><button type="submit">Review App registration on GitHub</button></form>',
                )
                return
            if parsed.path != "/callback":
                self.page(404, "Not found")
                return
            query = parse_qs(parsed.query)
            if not secrets.compare_digest(query.get("state", [""])[0], state):
                self.page(403, "Invalid registration state. Restart the helper.")
                return
            code = query.get("code", [""])[0]
            if not re.fullmatch(r"[A-Za-z0-9]{10,200}", code):
                self.page(400, "Missing or invalid registration code.")
                return
            try:
                conversion = subprocess.run(
                    ["gh", "api", "--method", "POST", f"/app-manifests/{code}/conversions"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                app = json.loads(conversion.stdout)
                metadata = save_and_configure(app, args.repo, ROOT / ".secrets")
                completed.update(done=True, ok=True)
                print(
                    json.dumps(
                        {key: value for key, value in metadata.items() if key != "key_path"}
                    ),
                    flush=True,
                )
                self.send_response(303)
                self.send_header("Location", metadata["install_url"])
                self.end_headers()
            except Exception as exc:
                completed["done"] = True
                print(
                    f"App registration/setup failed ({type(exc).__name__}). Check the App settings and local ignored secrets directory; no key was printed.",
                    flush=True,
                )
                self.page(
                    500,
                    "Setup did not finish. Inspect the helper's status and GitHub App settings before trying again.",
                )

    with HTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.timeout = 1
        url = f"http://127.0.0.1:{args.port}/start/{state}"
        print(f"Open the local registration page: {url}", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        deadline = time.monotonic() + 900
        while not completed["done"] and time.monotonic() < deadline:
            server.handle_request()
    return 0 if completed["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
