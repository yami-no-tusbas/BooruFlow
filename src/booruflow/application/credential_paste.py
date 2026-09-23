"""Parse Gelbooru's copyable API credential fragment without logging it."""

from urllib.parse import parse_qs, urlsplit


def parse_gelbooru_credentials(value: str) -> dict[str, str]:
    raw = value.strip()
    if "://" in raw:
        query = urlsplit(raw).query
    else:
        query = raw.lstrip("?&")
    params = parse_qs(query, keep_blank_values=False, strict_parsing=False)
    user_ids = params.get("user_id", [])
    keys = params.get("api_key", [])
    if len(user_ids) != 1 or len(keys) != 1 or not user_ids[0].isdigit() or not keys[0]:
        raise ValueError("Invalid Gelbooru credential parameters")
    return {"user_id": user_ids[0], "api_key": keys[0]}
