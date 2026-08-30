"""Run a local Qt Multimedia playback diagnostic without using the network."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, qVersion
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QApplication


def snapshot(player: QMediaPlayer, event: str, media: Path) -> None:
    print(
        "QT_MEDIA_DIAGNOSTIC "
        f"event={event}; media={media.name}; extension={media.suffix.casefold()}; "
        f"mediaStatus={player.mediaStatus().name}; playbackState={player.playbackState().name}; "
        f"error={player.error().name}; errorString={player.errorString()!r}; "
        f"duration={player.duration()}; hasVideo={player.hasVideo()}; hasAudio={player.hasAudio()}",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path)
    parser.add_argument("--timeout-ms", type=int, default=12_000)
    args = parser.parse_args(argv)
    media = args.media.resolve()
    if not media.is_file():
        parser.error(f"media does not exist: {media}")
    app = QApplication([])
    player = QMediaPlayer()
    audio = QAudioOutput()
    video = QVideoWidget()
    player.setAudioOutput(audio)
    player.setVideoOutput(video)
    print(f"QT_MEDIA_DIAGNOSTIC qt={qVersion()}; media={media.name}", flush=True)
    player.errorOccurred.connect(lambda *_: snapshot(player, "error", media))
    player.mediaStatusChanged.connect(lambda *_: snapshot(player, "media-status", media))
    player.playbackStateChanged.connect(lambda *_: snapshot(player, "playback-state", media))
    player.durationChanged.connect(lambda *_: snapshot(player, "duration", media))
    for name in ("videoTracksChanged", "audioTracksChanged"):
        signal = getattr(player, name, None)
        if signal is not None:
            signal.connect(lambda: snapshot(player, "tracks", media))
    video.show()
    player.setSource(QUrl.fromLocalFile(str(media)))
    player.play()
    QTimer.singleShot(args.timeout_ms, app.quit)
    result = app.exec()
    snapshot(player, "finished", media)
    player.stop()
    return result


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
