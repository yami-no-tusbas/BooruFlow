"""Eligibility for an explicit retry, independently of automatic retry budgets."""
from collections.abc import Mapping

from booruflow.domain.image_analysis import PublishState

MAX_AUTO_PUBLISH_ATTEMPTS = 3
_LEGACY_TRANSIENT_REASONS = {
    "publish_error", "publish_verification_failed", "unexpected_global_redirect",
}


def can_retry_publication(entry: Mapping[str, object]) -> bool:
    if (entry.get("publish_state") != PublishState.FAILED
            or entry.get("site") not in {"gelbooru", "e621"}
            or not entry.get("post_id")
            or not (entry.get("additions") or entry.get("removals"))
            or entry.get("failure_reason") == "locked_image"):
        return False
    flag = entry.get("failure_retryable")
    if flag is None or bool(flag):
        return True
    # Older Gelbooru runs recorded budget exhaustion as non-retryability.
    # Recover only known transient reasons at that old limit, never locks or
    # explicitly classified definitive validation failures.
    return (entry.get("site") == "gelbooru"
            and entry.get("failure_reason") in _LEGACY_TRANSIENT_REASONS
            and int(entry.get("publish_attempts", 0)) >= MAX_AUTO_PUBLISH_ATTEMPTS)
