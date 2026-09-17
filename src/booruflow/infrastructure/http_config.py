"""Shared non-secret identity for BooruFlow HTTP API requests."""

BOORUFLOW_USER_AGENT = "BooruFlow/0.1 (+https://github.com/yami-no-tusbas/BooruFlow)"


def e621_user_agent(username: str) -> str:
    """Build the identifiable User-Agent required by e621 without any API key."""
    safe_username = username.replace("\r", "").replace("\n", "").encode("ascii", "replace").decode()
    return f"BooruFlow/0.1 (by {safe_username} on e621; +https://github.com/yami-no-tusbas/BooruFlow)"
