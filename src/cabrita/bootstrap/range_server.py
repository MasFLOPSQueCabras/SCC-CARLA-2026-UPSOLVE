"""Standalone range-capable media server, stopped when its lease stdin closes."""

import argparse
import http.server
import re
import threading
from functools import partial
from pathlib import Path


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    timeout = 30
    remaining: int | None = None

    def log_message(self, format: str, *args: object) -> None:
        # Progress and failures are logged by the invoking lifecycle operation.
        pass

    def send_head(self):
        self.remaining = None
        header = self.headers.get("Range")
        if header is None:
            return super().send_head()
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            self.send_error(404)
            return None
        size = path.stat().st_size
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", header)
        if match is None:
            self.send_error(416)
            return None
        start = int(match[1])
        end = min(int(match[2]) if match[2] else size - 1, size - 1)
        if not 0 <= start <= end < size:
            self.send_error(416)
            return None
        stream = path.open("rb")
        stream.seek(start)
        self.remaining = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(self.remaining))
        self.end_headers()
        return stream

    def copyfile(self, source, outputfile) -> None:
        if self.remaining is None:
            super().copyfile(source, outputfile)
            return
        remaining = self.remaining
        while remaining > 0:
            data = source.read(min(remaining, 64 * 1024))
            if not data:
                break
            outputfile.write(data)
            remaining -= len(data)


if __name__ == "__main__":
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    args = parser.parse_args()
    directory = args.directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(
        (args.host, args.port), partial(RangeHandler, directory=str(directory))
    )

    def watch_lease() -> None:
        sys.stdin.buffer.read()
        server.shutdown()

    threading.Thread(target=watch_lease, daemon=True).start()
    print("READY", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
