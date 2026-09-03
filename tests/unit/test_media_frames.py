import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from booruflow.infrastructure.media_frames import (
    MediaFrameUnavailable,
    frame_timestamps,
    is_animated_gif,
    representative_frames,
)


class MediaFramesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_timestamps_use_the_four_requested_positions(self) -> None:
        self.assertEqual(frame_timestamps(20), (1.0, 5.0, 10.0, 19.0))

    def test_zero_or_missing_duration_is_rejected(self) -> None:
        with self.assertRaises(MediaFrameUnavailable):
            frame_timestamps(0)
        with self.assertRaises(MediaFrameUnavailable):
            frame_timestamps(-1)

    def test_static_image_is_not_sent_to_ffmpeg(self) -> None:
        image = self.root / "still.png"
        Image.new("RGB", (2, 2)).save(image)
        with patch("booruflow.infrastructure.media_frames.shutil.which") as which:
            frames, temporary, message = representative_frames(image)
        self.assertEqual(frames, [image])
        self.assertIsNone(temporary)
        self.assertEqual(message, "")
        which.assert_not_called()

    def test_animated_gif_extracts_representative_frames_and_cleans_up(self) -> None:
        gif = self.root / "animated.gif"
        images = [Image.new("RGB", (3, 3), (value, 0, 0)) for value in (255, 0, 0, 0)]
        images[0].save(gif, save_all=True, append_images=images[1:], duration=10, loop=0)
        self.assertTrue(is_animated_gif(gif))
        frames, temporary, _message = representative_frames(gif)
        self.assertEqual(len(frames), 4)
        self.assertGreater(Image.open(frames[0]).getpixel((0, 0))[0], 200)
        self.assertLess(Image.open(frames[-1]).getpixel((0, 0))[0], 10)
        root = Path(temporary.name)
        temporary.cleanup()
        self.assertFalse(root.exists())

    def test_video_without_ffmpeg_is_never_returned_as_an_image(self) -> None:
        video = self.root / "clip.webm"
        video.touch()
        with patch("booruflow.infrastructure.media_frames.shutil.which", return_value=None), self.assertRaisesRegex(
            MediaFrameUnavailable, "ffmpeg"
        ):
            representative_frames(video)

    def test_non_executable_ffmpeg_fails_without_leaking_temporary_files(self) -> None:
        video = self.root / "clip.mp4"
        video.touch()
        with patch("booruflow.infrastructure.media_frames.shutil.which", side_effect=["ffmpeg", "ffprobe"]), patch(
            "booruflow.infrastructure.media_frames.subprocess.check_output", return_value="2"
        ), patch("booruflow.infrastructure.media_frames.subprocess.run", side_effect=OSError("denied")), self.assertRaisesRegex(
            MediaFrameUnavailable, "frame extraction"
        ):
            representative_frames(video)
        self.assertEqual(list(self.root.glob("booruflow-video-*")), [])

    def test_missing_or_failed_frame_cleans_up_and_stops_extraction(self) -> None:
        video = self.root / "clip.mkv"
        video.touch()
        calls = []

        def extraction(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1)

        with patch("booruflow.infrastructure.media_frames.shutil.which", side_effect=["ffmpeg", "ffprobe"]), patch(
            "booruflow.infrastructure.media_frames.subprocess.check_output", return_value="0.1"
        ), patch("booruflow.infrastructure.media_frames.subprocess.run", side_effect=extraction), self.assertRaises(
            MediaFrameUnavailable
        ):
            representative_frames(video)
        self.assertEqual(len(calls), 1)
        self.assertEqual(list(self.root.glob("booruflow-video-*")), [])

    def test_short_video_uses_four_bounded_timestamps_and_success_cleans_up(self) -> None:
        video = self.root / "clip.mov"
        video.touch()
        commands = []

        def extraction(command, **_kwargs):
            commands.append(command)
            Path(command[-1]).touch()
            return subprocess.CompletedProcess(command, 0)

        with patch("booruflow.infrastructure.media_frames.shutil.which", side_effect=["ffmpeg", "ffprobe"]), patch(
            "booruflow.infrastructure.media_frames.subprocess.check_output", return_value="0.04"
        ), patch("booruflow.infrastructure.media_frames.subprocess.run", side_effect=extraction):
            frames, temporary, _message = representative_frames(video)
        self.assertEqual([command[3] for command in commands], ["0.002", "0.01", "0.02", "0.038"])
        self.assertTrue(all(frame.is_file() for frame in frames))
        root = Path(temporary.name)
        temporary.cleanup()
        self.assertFalse(root.exists())

    def test_unsupported_extension_is_an_ordinary_single_image_path(self) -> None:
        unknown = self.root / "clip.avi"
        unknown.touch()
        self.assertEqual(representative_frames(unknown), ([unknown], None, ""))
