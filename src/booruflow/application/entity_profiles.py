"""Read-only Gelbooru sampling; no image acquisition or publication."""
from __future__ import annotations

import urllib.error
from dataclasses import dataclass
from threading import Event

from booruflow.domain.auto_organize import PostMetadata
from booruflow.domain.booru_sites import category_name
from booruflow.infrastructure.gelbooru_normalization import normalize_post, post_tags
from booruflow.infrastructure.tag_browser import exact_tags


@dataclass(frozen=True, slots=True)
class EntityRefreshResult:
    new_posts: int
    total_posts: int
    state: str


class EntityProfileAnalyzer:
    def __init__(self, repository, client, cache, tag_database=None, *,
                 throttle_seconds=1.0, retry_delays=(2, 5, 10)):
        self.repository = repository
        self.client = client
        self.cache = cache
        self.tag_database = tag_database
        self.throttle_seconds = throttle_seconds
        self.retry_delays = retry_delays

    def _page(self, query, page, cancelled: Event, progress):
        for attempt in range(len(self.retry_delays) + 1):
            if cancelled.is_set():
                return []
            try:
                return self.client.search_posts(query, limit=100, page=page)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code not in {429, 500, 502, 503, 504}:
                    raise
                if attempt == len(self.retry_delays):
                    raise
                if progress:
                    progress({"phase": "retry", "page": page, "attempt": attempt + 1})
                if cancelled.wait(self.retry_delays[attempt]):
                    return []

    def _metadata(self, posts):
        posts = [normalize_post(post) for post in posts]
        all_names = sorted({tag for post in posts for tag in post_tags(post)})
        local_categories = {}
        if self.tag_database is not None and self.tag_database.is_file():
            local_categories = {row.name: category_name("gelbooru", row.category)
                                for row in exact_tags(self.tag_database, all_names)}
        result = []
        for post in posts:
            post_id = str(post["id"])
            cached = self.cache.get("gelbooru", post_id)
            # Search already supplies tags. No id: request is necessary, even on a cache miss.
            tags = tuple(post_tags(post))
            categories = dict(cached.categories) if cached else {}
            categories.update(post.get("_tag_categories", {}) or {})
            for field, category in (("character", "character"), ("copyright", "copyright"),
                                    ("artist", "artist"), ("general", "general"), ("meta", "meta")):
                categories.update((tag, category) for tag in str(post.get(f"tag_string_{field}", "")).split())
            categories.update({tag: local_categories[tag] for tag in tags if tag in local_categories})
            categories = {tag: category_name("gelbooru", categories.get(tag)) for tag in tags}
            extra = dict(cached.extra) if cached else {}
            if post.get("preview_url"):
                extra["preview_url"] = post["preview_url"]
            metadata = PostMetadata("gelbooru", post_id, tags, categories,
                                    rating=str(post.get("rating", "")),
                                    source=str(post.get("source", "")), md5=str(post.get("md5", "")),
                                    extra=extra)
            self.cache.put(metadata)
            result.append(metadata)
        return result

    def refresh(self, profile_id: int, new_posts: int, *, cancelled=None, progress=None):
        if not 1 <= new_posts <= 10000:
            raise ValueError("Choose between 1 and 10000 new posts")
        cancelled = cancelled if cancelled is not None else Event()
        profile = self.repository.profile(profile_id)
        seen = self.repository.seen_ids(profile_id)
        added, page = 0, 0
        previous_page_ids = None
        state = "completed"
        self.repository.begin_run(profile_id)
        try:
            while added < new_posts and not cancelled.is_set():
                posts = self._page(profile["query"], page, cancelled, progress)
                if cancelled.is_set():
                    break
                if not posts:
                    state = "exhausted"
                    break
                ids = tuple(str(post.get("id", "")) for post in posts)
                if ids == previous_page_ids:
                    raise RuntimeError("Gelbooru repeated the same page; refresh stopped safely")
                previous_page_ids = ids
                fresh = []
                page_seen = set(seen)
                for post in posts:
                    post_id = str(post.get("id", ""))
                    if not post_id.isdigit() or int(post_id) <= 0:
                        raise ValueError("Gelbooru returned an invalid post ID")
                    if post_id not in page_seen:
                        if "tags" not in post and "tag_string" not in post:
                            raise ValueError("Gelbooru returned a post without tags")
                        fresh.append(post)
                        page_seen.add(post_id)
                    if len(fresh) >= new_posts - added:
                        break
                metadata = self._metadata(fresh)
                if cancelled.is_set():
                    break
                added += self.repository.add_posts(profile_id, metadata)
                seen.update(post.post_id for post in metadata)
                if progress:
                    progress({"phase": "sample", "new": added, "requested": new_posts, "page": page})
                if len(posts) < 100 and added < new_posts:
                    state = "exhausted"
                    break
                page += 1
                if added < new_posts and cancelled.wait(self.throttle_seconds):
                    break
            if cancelled.is_set():
                state = "cancelled"
        except Exception:
            self.repository.finish_run(profile_id, "failed")
            raise
        self.repository.finish_run(profile_id, state)
        return EntityRefreshResult(added, self.repository.profile(profile_id)["sample_count"], state)


def merge_profile_suggestions(rows, suggestions, *, decisions=None, category_values=None):
    """Retain individual scores. Existing WD14 confidence always stays visible."""
    decisions = decisions or {}
    for suggestion in suggestions:
        key = suggestion.tag
        origin = f"{suggestion.entity_tag} profile: {suggestion.frequency:.1%}"
        row = rows.get(key)
        decision = decisions.get(key, {})
        if row is None:
            category, category_id = (category_values(suggestion.category) if category_values
                                     else (suggestion.category, None))
            row = rows[key] = {
                "id": f"entity:{key}", "tag": key, "confidence": f"{suggestion.frequency:.3f}",
                "decision": decision.get("decision", "unreviewed"), "match": origin,
                "category": category, "category_id": category_id,
            }
        else:
            if origin not in row["match"]:
                row["match"] += f" · {origin}"
            if str(row["id"]) == str(decision.get("observation_id")):
                # An explicit profile decision reuses a manual observation, including undo/redo.
                row["id"] = f"entity:{key}"
                if "wd14_score" not in row:
                    row["confidence"] = f"{suggestion.frequency:.3f}"
        row.setdefault("profile_scores", []).append({
            "profile_id": suggestion.profile_id, "entity_tag": suggestion.entity_tag,
            "frequency": suggestion.frequency,
        })
