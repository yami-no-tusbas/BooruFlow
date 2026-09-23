"""Isolated read-only thumbnail pipeline probe for source and portable builds."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QLabel

from booruflow.presentation.pyside6.thumbnail_cache import ThumbnailCacheKey, ThumbnailMemoryCache
from booruflow.presentation.pyside6.thumbnail_loader import ThumbnailLoader


def _local_server() -> ThreadingHTTPServer:
    image = QImage(8, 8, QImage.Format.Format_RGBA8888)
    image.fill(0xFF4488CC)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Could not create probe PNG")
    buffer.close()
    payload = bytes(data)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("network", "decode", "pixmap", "widget"), required=True)
    parser.add_argument("--count", type=int, default=34)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--url", default="")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    server = None if args.url else _local_server()
    url = args.url or f"http://127.0.0.1:{server.server_port}/thumbnail.png"
    stage = args.stage if args.stage in {"network", "decode"} else "full"
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(), concurrency=args.concurrency,
        max_retries=0, diagnostic_stage=stage,
    )
    widgets = [QLabel() for _ in range(args.count)] if args.stage == "widget" else []
    applied = 0
    finished = False

    def on_image(key, image):
        nonlocal applied
        print(f"PROBE pixmap conversion start post_id={key.post_id}", flush=True)
        pixmap = QPixmap.fromImage(image)
        print(f"PROBE pixmap conversion end post_id={key.post_id} null={pixmap.isNull()}", flush=True)
        if args.stage == "widget":
            print(f"PROBE widget apply start post_id={key.post_id}", flush=True)
            widgets[key.post_id].setPixmap(pixmap)
            print(f"PROBE widget apply end post_id={key.post_id}", flush=True)
        applied += 1

    def on_diagnostic(_level, message):
        nonlocal finished
        print(message, flush=True)
        if message.startswith("Thumbnail load complete"):
            finished = True
            app.quit()

    loader.image_ready.connect(on_image)
    loader.diagnostic.connect(on_diagnostic)
    keys = [ThumbnailCacheKey("gelbooru", index, url) for index in range(args.count)]
    QTimer.singleShot(0, lambda: loader.begin_wave(keys))
    QTimer.singleShot(30_000, app.quit)
    try:
        app.exec()
    finally:
        loader.cancel()
        if server is not None:
            server.shutdown()
            server.server_close()
    print(f"PROBE result stage={args.stage} finished={finished} network_success={loader._network_success} applied={applied}", flush=True)
    return 0 if finished and loader._network_success == args.count and (
        args.stage in {"network", "decode"} or applied == args.count
    ) else 3


if __name__ == "__main__":
    raise SystemExit(main())
