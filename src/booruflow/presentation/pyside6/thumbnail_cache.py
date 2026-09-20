"""Session-scoped, memory-only thumbnail cache for Tagging."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtGui import QImage

DEFAULT_THUMBNAIL_CACHE_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ThumbnailCacheKey:
    site: str
    post_id: int
    url: str


class ThumbnailMemoryCache:
    """Byte-bounded LRU of decoded images; it never reads or writes files."""

    def __init__(self, max_bytes: int = DEFAULT_THUMBNAIL_CACHE_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = int(max_bytes)
        self._images: OrderedDict[ThumbnailCacheKey, tuple[QImage, int]] = OrderedDict()
        self._bytes = 0

    @property
    def byte_count(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._images)

    def get(self, key: ThumbnailCacheKey) -> QImage | None:
        cached = self._images.get(key)
        if cached is None:
            return None
        self._images.move_to_end(key)
        return cached[0]

    def put(self, key: ThumbnailCacheKey, image: QImage) -> bool:
        if image.isNull():
            return False
        stored = image.copy()
        cost = max(1, int(stored.sizeInBytes()))
        previous = self._images.pop(key, None)
        if previous is not None:
            self._bytes -= previous[1]
        if cost > self.max_bytes:
            return False
        self._images[key] = (stored, cost)
        self._bytes += cost
        while self._bytes > self.max_bytes:
            _old_key, (_old_image, old_cost) = self._images.popitem(last=False)
            self._bytes -= old_cost
        return True

    def clear(self) -> None:
        self._images.clear()
        self._bytes = 0
