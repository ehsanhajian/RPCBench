"""Display helpers. Never print API keys or passwords from URLs."""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SECRET_QUERY_KEYS = {
    "apikey",
    "api_key",
    "key",
    "token",
    "auth",
    "password",
    "secret",
    "jwt",
    "access_token",
    "x-api-key",
}


def url_fingerprint(url: str) -> str:
    """Short hash of the raw URL so reports can correlate without printing secrets."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def display_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    netloc = parts.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[-1]
        netloc = f"***@{host}"
    path = _redact_path(parts.path)
    query = parts.query
    if query:
        pairs = []
        for key, value in parse_qsl(query, keep_blank_values=True):
            if key.lower() in _SECRET_QUERY_KEYS:
                pairs.append(f"{key}=[redacted]")
            else:
                pairs.append(urlencode({key: value}))
        query = "&".join(pairs)
    return urlunsplit((parts.scheme, netloc, path, query, parts.fragment))


def _redact_path(path: str) -> str:
    parts = path.split("/")
    out: list[str] = []
    for part in parts:
        if _looks_like_key(part):
            out.append("[redacted]")
        else:
            out.append(part)
    return "/".join(out)


def _looks_like_key(part: str) -> bool:
    if len(part) < 16:
        return False
    return all(c.isalnum() or c in "-_" for c in part)
