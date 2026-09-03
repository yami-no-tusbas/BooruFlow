"""Bounded representative-frame helpers for animated and video analysis."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

POSITIONS = (0.05, 0.25, 0.50, 0.95)
VIDEO_SUFFIXES = frozenset({".webm", ".mp4", ".mov", ".mkv"})


class MediaFrameUnavailable(RuntimeError):
    """A video could not be safely converted to images for analysis."""


def is_video(path: Path) -> bool:
    return path.suffix.casefold() in VIDEO_SUFFIXES


def is_animated_gif(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            return path.suffix.casefold() == ".gif" and int(getattr(image, "n_frames", 1)) > 1
    except OSError:
        return False


def frame_timestamps(duration: float) -> tuple[float, ...]:
    """Return the four bounded timestamps used for a positive video duration."""
    if duration <= 0:
        raise MediaFrameUnavailable("Video analysis unavailable: invalid or missing duration")
    return tuple(duration * position for position in POSITIONS)


def representative_frames(path: Path) -> tuple[list[Path], tempfile.TemporaryDirectory | None, str]:
    """Return local image paths, never returning a video file as an analysis input."""
    if is_animated_gif(path):
        temporary = tempfile.TemporaryDirectory(prefix="booruflow-gif-")
        root = Path(temporary.name)
        frames: list[Path] = []
        try:
            with Image.open(path) as image:
                total = int(image.n_frames)
                for index, position in enumerate(POSITIONS):
                    image.seek(min(total - 1, round((total - 1) * position)))
                    target = root / f"frame-{index}.png"
                    image.convert("RGB").save(target)
                    frames.append(target)
        except Exception:
            temporary.cleanup()
            raise
        return frames, temporary, "GIF multi-frame analysis: 4 frames extracted"

    if not is_video(path):
        return [path], None, ""

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise MediaFrameUnavailable(
            "Video analysis unavailable: ffmpeg and ffprobe are required; "
            "a preview image may still be used when the source provides one"
        )
    try:
        duration = float(
            subprocess.check_output(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                text=True,
                timeout=15,
            ).strip()
        )
        timestamps = frame_timestamps(duration)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise MediaFrameUnavailable("Video analysis unavailable: duration could not be read") from exc

    temporary = tempfile.TemporaryDirectory(prefix="booruflow-video-")
    root = Path(temporary.name)
    frames: list[Path] = []
    try:
        for index, timestamp in enumerate(timestamps):
            target = root / f"frame-{index}.png"
            result = subprocess.run(
                [ffmpeg, "-y", "-ss", str(timestamp), "-i", str(path), "-frames:v", "1", str(target)],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode or not target.is_file():
                raise MediaFrameUnavailable("Video analysis unavailable: frame extraction failed")
            frames.append(target)
    except (OSError, subprocess.SubprocessError, MediaFrameUnavailable) as exc:
        temporary.cleanup()
        if isinstance(exc, MediaFrameUnavailable):
            raise
        raise MediaFrameUnavailable("Video analysis unavailable: frame extraction failed") from exc
    return frames, temporary, "Video multi-frame analysis: 4 frames extracted"
