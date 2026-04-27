"""
Plexus CLI — `plexus init` style auth, plus a few sibling commands.

Designed to feel like fly.io / vercel CLIs:
    $ pip install plexus
    $ plexus init
    Opening browser to https://app.plexus.company/auth/cli...
    ✓ Saved API key as cli-<host>. You're set up.

Implementation:
- Spin up a local HTTP listener on a random free port.
- Open the browser to /auth/cli with the callback URL embedded.
- Block until the browser POSTs (well — redirects with key) to /callback.
- Verify the `state` parameter matches what we generated.
- Persist the key via plexus.config.save_config; the SDK already reads
  `~/.plexus/config.json` for `PLEXUS_API_KEY`.

Stdlib only — keep dependency footprint minimal.
"""

from __future__ import annotations

import argparse
import http.server
import secrets
import socket
import socketserver
import sys
import threading
import urllib.parse
import webbrowser
from typing import Optional

from . import config


DEFAULT_TIMEOUT_SECONDS = 300
SUCCESS_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Plexus CLI</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      :root { color-scheme: light dark; }
      body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        display: flex; align-items: center; justify-content: center;
        min-height: 100vh; margin: 0; background: Canvas; color: CanvasText;
      }
      .card {
        max-width: 360px; padding: 32px; border: 1px solid #8884;
        border-radius: 12px; text-align: center;
      }
      h1 { margin: 0 0 8px; font-size: 18px; }
      p { margin: 0; color: #888; font-size: 14px; }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>You're all set</h1>
      <p>Return to your terminal &mdash; the CLI has your key.</p>
    </div>
  </body>
</html>""".encode("utf-8")

ERROR_HTML = """<!doctype html>
<html><head><meta charset="utf-8" /><title>Plexus CLI</title></head>
<body><pre style="font-family:ui-monospace,monospace;padding:24px">
Plexus CLI authorization failed. Return to your terminal for details.
</pre></body></html>""".encode("utf-8")


class _CallbackResult:
    key: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_handler(result: _CallbackResult, expected_state: str, done: threading.Event):
    class Handler(http.server.BaseHTTPRequestHandler):
        # Silence the default request log — we don't want CLI noise.
        def log_message(self, *_args, **_kwargs):  # type: ignore[override]
            return

        def do_GET(self):  # type: ignore[override]
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            params = urllib.parse.parse_qs(parsed.query)
            got_state = (params.get("state") or [""])[0]
            got_key = (params.get("key") or [""])[0]

            if got_state != expected_state:
                result.error = "state mismatch"
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(ERROR_HTML)
                done.set()
                return

            if not got_key:
                result.error = "no key in callback"
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(ERROR_HTML)
                done.set()
                return

            result.key = got_key
            result.state = got_state
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(SUCCESS_HTML)
            done.set()

    return Handler


def _hostname_label() -> str:
    try:
        host = socket.gethostname() or "device"
    except Exception:
        host = "device"
    # Strip the trailing .local etc. and clean it for display.
    safe = host.split(".")[0].lower().replace(" ", "-")
    return f"cli-{safe}" if safe else "cli"


def cmd_init(args: argparse.Namespace) -> int:
    """Open the browser, capture an API key, save it locally."""
    existing = config.get_api_key()
    if existing and not args.force:
        print(
            "An API key is already configured. "
            "Re-run with --force to replace it.",
            file=sys.stderr,
        )
        return 1

    endpoint = config.get_endpoint().rstrip("/")
    state = secrets.token_urlsafe(24)
    name = args.name or _hostname_label()
    port = _pick_free_port()
    callback = f"http://127.0.0.1:{port}/callback"

    auth_url = (
        f"{endpoint}/auth/cli"
        f"?state={urllib.parse.quote(state)}"
        f"&callback={urllib.parse.quote(callback)}"
        f"&name={urllib.parse.quote(name)}"
    )

    result = _CallbackResult()
    done = threading.Event()
    handler = _make_handler(result, state, done)

    server = socketserver.TCPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        print(f"Opening {auth_url}")
        try:
            webbrowser.open(auth_url, new=1, autoraise=True)
        except Exception:
            pass  # User can copy the URL manually.

        print("Waiting for browser confirmation...", flush=True)
        finished = done.wait(timeout=args.timeout)
        if not finished:
            print(
                f"Timed out after {args.timeout}s. Re-run `plexus init`.",
                file=sys.stderr,
            )
            return 2
    finally:
        server.shutdown()
        server.server_close()

    if result.error or not result.key:
        print(
            f"Authorization failed: {result.error or 'no key returned'}",
            file=sys.stderr,
        )
        return 3

    cfg = config.load_config()
    cfg["api_key"] = result.key
    config.save_config(cfg)
    print(f"✓ Saved API key as {name}.")
    print("  ~/.plexus/config.json")
    return 0


def cmd_logout(_args: argparse.Namespace) -> int:
    """Forget the locally stored API key."""
    cfg = config.load_config()
    if not cfg.get("api_key"):
        print("Nothing to do — no key on file.")
        return 0
    cfg["api_key"] = None
    config.save_config(cfg)
    print("✓ Cleared local API key.")
    return 0


def cmd_whoami(_args: argparse.Namespace) -> int:
    """Print the prefix of the locally stored key + the configured endpoint."""
    key = config.get_api_key()
    endpoint = config.get_endpoint()
    if not key:
        print("Not signed in. Run `plexus init` to authorize this machine.")
        return 1
    masked = f"{key[:8]}…{key[-4:]}" if len(key) > 12 else key
    print(f"key:      {masked}")
    print(f"endpoint: {endpoint}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plexus",
        description="Plexus CLI — auth, send, query telemetry from your terminal.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser(
        "init",
        help="Authorize this machine and save an API key locally.",
        aliases=["login"],
    )
    init.add_argument("--name", help="Label for the issued key (default: cli-<hostname>).")
    init.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Seconds to wait for the browser callback.",
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing local key.",
    )
    init.set_defaults(func=cmd_init)

    logout = sub.add_parser("logout", help="Forget the local API key.")
    logout.set_defaults(func=cmd_logout)

    whoami = sub.add_parser("whoami", help="Show the local credential summary.")
    whoami.set_defaults(func=cmd_whoami)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
