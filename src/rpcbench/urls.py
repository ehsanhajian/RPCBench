"""Display helpers. Never print API keys or passwords from URLs."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SECRET_QUERY_KEYS = {
    "apikey",
    "api_key",
    "key",
    "token",
    "auth",
    "password",
    "secret",
}


def display_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    netloc = parts.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[-1]
        netloc = f"***@{host}"
    query = parts.query
    if query:
        pairs = []
        for key, value in parse_qsl(query, keep_blank_values=True):
            if key.lower() in _SECRET_QUERY_KEYS:
                pairs.append(f"{key}=[redacted]")
            else:
                pairs.append(urlencode({key: value}))
        query = "&".join(pairs)
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
