"""Serve the built static demo (demo-static/) locally, as an offline backup for the public Vercel site.

Standard library only. Unknown paths fall back to index.html, as the Vercel rewrite does, so reloading a patient page
works. Usage: python scripts/serve_static_demo.py [--port 4173]   then open http://127.0.0.1:4173/
"""
import argparse
import functools
import http.server
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "demo-static"


class Handler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        path = Path(self.translate_path(self.path))
        if not path.exists():
            self.path = "/index.html"
        return super().send_head()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=4173)
    args = ap.parse_args()
    if not (SITE / "index.html").exists():
        raise SystemExit("demo-static/ is missing; build it with `pnpm build:static` in dashboard/web")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), functools.partial(Handler, directory=str(SITE)))
    print(f"Static AKI demo: http://127.0.0.1:{args.port}/  (Ctrl+C to stop)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
