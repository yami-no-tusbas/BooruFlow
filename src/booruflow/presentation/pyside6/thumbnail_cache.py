"""Session-scoped, memory-only thumbnail cache for Tagging."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtGui import QImage

DEFAULT_THUMBNAIL_CACHE_BYTES = 128 * 1024 * 1024
THUMBNAIL_CACHE_LIMITS = tuple(value * 1024 * 1024 for value in (
    128, 192, 256, 320, 384, 512, 640, 768, 1024,
))
MAX_ADAPTIVE_THUMBNAIL_CACHE_BYTES = THUMBNAIL_CACHE_LIMITS[-1]


@dataclass(frozen=True, slots=True)
class ThumbnailCacheKey:
    site: str
    post_id: int
    url: str


class ThumbnailMemoryCache:
    """Byte-bounded LRU of decoded images; it never reads or writes files."""

    def __init__(self, max_bytes: int | None = None, *, max_items: int | None = None) -> None:
        self.adaptive = max_bytes is None
        if max_bytes is None:
            max_bytes = DEFAULT_THUMBNAIL_CACHE_BYTES
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = int(max_bytes)
        self.max_items = max_items
        self._images: OrderedDict[ThumbnailCacheKey, tuple[QImage, int]] = OrderedDict()
        self._bytes = 0
        self._expected_items = 0
        self._observed_items = 0
        self._observed_bytes = 0
        self._low_usage_rounds = 0
        self.evictions = 0
        self.hits = 0
        self.misses = 0

    @property
    def byte_count(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._images)

    def get(self, key: ThumbnailCacheKey) -> QImage | None:
        cached = self._images.get(key)
        if cached is None:
            self.misses += 1
            return None
        self.hits += 1
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
        while self._bytes > self.max_bytes or (
            self.max_items is not None and len(self._images) > self.max_items
        ):
            _old_key, (_old_image, old_cost) = self._images.popitem(last=False)
            self._bytes -= old_cost
            self.evictions += 1
        return True

    @property
    def target_bytes(self) -> int:
        return self.max_bytes

    @property
    def average_image_bytes(self) -> int:
        return int(self._observed_bytes / self._observed_items) if self._observed_items else 0

    def prepare_for_items(self, item_count: int) -> None:
        """Set a working-set hint without allocating or reserving memory."""
        if not self.adaptive:
            return
        self._expected_items = max(0, int(item_count))
        self._grow_to_estimate()

    def observe_image(self, image: QImage) -> None:
        if not self.adaptive or image.isNull():
            return
        self._observed_items += 1
        self._observed_bytes += max(1, int(image.sizeInBytes()))
        self._grow_to_estimate()

    def settle_working_set(self, *, calm: bool = False) -> None:
        """Shrink only after repeated under-use, never during a hot wave."""
        if not self.adaptive or not calm:
            return
        if self._expected_items and len(self) <= max(1, self._expected_items // 2):
            self._low_usage_rounds += 1
        else:
            self._low_usage_rounds = 0
        if self._low_usage_rounds < 2:
            return
        average = self.average_image_bytes
        if not average:
            return
        target = self._round_capacity(self._expected_items * average * 1.20)
        self._resize(max(DEFAULT_THUMBNAIL_CACHE_BYTES, target))
        self._low_usage_rounds = 0

    def _grow_to_estimate(self) -> None:
        average = self.average_image_bytes
        if not average or not self._expected_items:
            return
        target = self._round_capacity(self._expected_items * average * 1.20)
        if target > self.max_bytes:
            self._resize(target)

    @staticmethod
    def _round_capacity(value: float) -> int:
        return min(
            MAX_ADAPTIVE_THUMBNAIL_CACHE_BYTES,
            next((limit for limit in THUMBNAIL_CACHE_LIMITS if value <= limit), MAX_ADAPTIVE_THUMBNAIL_CACHE_BYTES),
        )

    def _resize(self, max_bytes: int) -> None:
        self.max_bytes = max(DEFAULT_THUMBNAIL_CACHE_BYTES, int(max_bytes))
        while self._bytes > self.max_bytes and self._images:
            _old_key, (_old_image, old_cost) = self._images.popitem(last=False)
            self._bytes -= old_cost
            self.evictions += 1

    def clear(self) -> None:
        self._images.clear()
        self._bytes = 0
        self._expected_items = self._observed_items = self._observed_bytes = 0
        self._low_usage_rounds = 0
