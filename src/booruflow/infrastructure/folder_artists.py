"""Fast, read-only artist counts for a downloaded image folder."""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from booruflow.infrastructure.retro_cleanup import IMAGE_EXTENSIONS


@dataclass(frozen=True, slots=True)
class ArtistCount:
    name: str
    images: int


@dataclass(frozen=True, slots=True)
class FolderArtistScan:
    root: Path
    artists: tuple[ArtistCount, ...]
    images_found: int
    images_with_artist: int
    anonymous_ignored: int
    unrecognized_names: int
    inaccessible_entries: int = 0
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ImageCountFilter:
    minimum: int | None = None
    maximum: int | None = None

    def matches(self, value: int) -> bool:
        return (self.minimum is None or value >= self.minimum) and (
            self.maximum is None or value <= self.maximum
        )


_COUNT_COMPARISON = re.compile(r"(<=|>=|<|>)?\s*(\d+)")
_COUNT_RANGE = re.compile(r"(\d+)\s*~\s*(\d+)")


def parse_image_count_filter(expression: str) -> ImageCountFilter | None:
    """Parse the small DB Browser-like numeric filter language.

    An empty expression disables filtering. Invalid or descending ranges raise
    ``ValueError`` so the UI can preserve the last valid filter while typing.
    """
    value = expression.strip()
    if not value:
        return None
    if match := _COUNT_RANGE.fullmatch(value):
        minimum, maximum = (int(part) for part in match.groups())
        if minimum > maximum:
            raise ValueError("range minimum exceeds maximum")
        return ImageCountFilter(minimum, maximum)
    if match := _COUNT_COMPARISON.fullmatch(value):
        operator, number_text = match.groups()
        number = int(number_text)
        if operator == "<":
            return ImageCountFilter(maximum=number - 1)
        if operator == "<=":
            return ImageCountFilter(maximum=number)
        if operator == ">":
            return ImageCountFilter(minimum=number + 1)
        if operator == ">=":
            return ImageCountFilter(minimum=number)
        return ImageCountFilter(number, number)
    raise ValueError("invalid image-count filter")


def extract_artist(filename: str) -> str | None:
    """Return the prefix from a minimally recognizable four-part Grabber name."""
    stem = Path(filename).stem
    parts = stem.split(" - ", 3)
    if len(parts) != 4:
        return None
    artist = parts[0].strip()
    if not artist:
        return None
    return artist


def scan_folder_artists(
    root: Path,
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int], None] | None = None,
) -> FolderArtistScan:
    """Recursively count filename artists without opening image contents.

    ``os.scandir`` avoids materializing large directory listings and lets an
    inaccessible entry be skipped without losing the rest of the collection.
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    counts: Counter[str] = Counter()
    display_names: dict[str, str] = {}
    images_found = 0
    anonymous_ignored = 0
    unrecognized_names = 0
    inaccessible_entries = 0
    was_cancelled = False
    pending = [root]

    while pending:
        if cancelled and cancelled():
            was_cancelled = True
            break
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            inaccessible_entries += 1
            continue
        with entries:
            iterator = iter(entries)
            while True:
                try:
                    entry = next(iterator)
                except StopIteration:
                    break
                except OSError:
                    inaccessible_entries += 1
                    continue
                if cancelled and cancelled():
                    was_cancelled = True
                    break
                try:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    inaccessible_entries += 1
                    continue
                if Path(entry.name).suffix.casefold() not in IMAGE_EXTENSIONS:
                    continue

                images_found += 1
                artist = extract_artist(entry.name)
                if artist is None:
                    unrecognized_names += 1
                elif artist.casefold() == "anonymous":
                    anonymous_ignored += 1
                else:
                    key = artist.casefold()
                    display_names.setdefault(key, artist)
                    counts[key] += 1
                if progress and (images_found == 1 or images_found % 500 == 0):
                    progress(images_found)
            if was_cancelled:
                break

    artists = tuple(
        ArtistCount(display_names[key], count)
        for key, count in sorted(
            counts.items(), key=lambda item: (-item[1], display_names[item[0]].casefold())
        )
    )
    return FolderArtistScan(
        root=root,
        artists=artists,
        images_found=images_found,
        images_with_artist=sum(counts.values()),
        anonymous_ignored=anonymous_ignored,
        unrecognized_names=unrecognized_names,
        inaccessible_entries=inaccessible_entries,
        cancelled=was_cancelled,
    )
