"""Polite, cached HTTP client shared by every source adapter.

Responsibilities kept in one place so no adapter has to re-implement them:

* on-disk response cache, so re-runs never re-hit the source (the brief asks for this
  explicitly, and it also makes the pipeline reproducible offline once warmed)
* retry with exponential backoff on transient failures only
* a fixed delay between live requests, so we stay a polite client
* a User-Agent that identifies who is calling and why

Cache keys are a hash of method + URL + body, so a POST search with different
criteria is a different cache entry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Final

import requests

from .config import (
    CACHE_DIR,
    MAX_RETRIES,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

# Status codes worth retrying: transient server and rate-limit conditions.
# A 403/404 is a definitive answer and must not be retried.
_RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})


class HttpClient:
    """Caching HTTP client with backoff and rate limiting."""

    def __init__(self, cache_dir: Path = CACHE_DIR, *, use_cache: bool = True) -> None:
        self._cache_dir = cache_dir
        self._use_cache = use_cache
        self._last_request_at: float = 0.0
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ cache --

    def _cache_path(self, method: str, url: str, body: bytes | None, suffix: str) -> Path:
        digest = hashlib.sha256(
            b"|".join([method.encode(), url.encode(), body or b""])
        ).hexdigest()[:24]
        return self._cache_dir / f"{digest}{suffix}"

    def _throttle(self) -> None:
        """Sleep so that consecutive *live* requests are spaced politely apart."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS - elapsed)
        self._last_request_at = time.monotonic()

    # ----------------------------------------------------------------- request --

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            try:
                response = self._session.request(
                    method,
                    url,
                    data=body,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("%s %s failed (attempt %d): %s", method, url, attempt, exc)
            else:
                if response.status_code not in _RETRYABLE_STATUS:
                    return response
                last_error = requests.HTTPError(
                    f"{response.status_code} for {url}", response=response
                )
                logger.warning(
                    "%s %s returned %d (attempt %d)",
                    method,
                    url,
                    response.status_code,
                    attempt,
                )

            if attempt < MAX_RETRIES:
                backoff = REQUEST_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.info("backing off %.1fs before retry", backoff)
                time.sleep(backoff)

        raise RuntimeError(f"{method} {url} failed after {MAX_RETRIES} attempts") from last_error

    # ------------------------------------------------------------------- public --

    def get_json(self, url: str) -> Any:
        """GET a JSON document, using the cache when available."""
        cache_path = self._cache_path("GET", url, None, ".json")
        if self._use_cache and cache_path.exists():
            logger.debug("cache hit %s", url)
            return json.loads(cache_path.read_text(encoding="utf-8"))

        response = self._request("GET", url, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return payload

    def post_json(self, url: str, payload: dict[str, Any]) -> Any:
        """POST a JSON body and decode a JSON response, using the cache when available."""
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        cache_path = self._cache_path("POST", url, body, ".json")
        if self._use_cache and cache_path.exists():
            logger.debug("cache hit POST %s", url)
            return json.loads(cache_path.read_text(encoding="utf-8"))

        response = self._request(
            "POST",
            url,
            body=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        response.raise_for_status()
        result = response.json()
        cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return result

    def get_bytes(self, url: str) -> bytes | None:
        """Fetch a binary document (spreadsheet, PDF).

        Returns None instead of raising when the document is walled off, so that a
        single inaccessible attachment never aborts a pipeline run. Callers record
        the URL and carry on, which is what the brief asks for.
        """
        cache_path = self._cache_path("GET", url, None, ".bin")
        if self._use_cache and cache_path.exists():
            return cache_path.read_bytes()

        try:
            response = self._request("GET", url)
        except RuntimeError:
            logger.warning("giving up on document %s", url)
            return None

        if response.status_code != 200:
            logger.warning(
                "document %s not retrievable (HTTP %d) - recording and continuing",
                url,
                response.status_code,
            )
            return None

        cache_path.write_bytes(response.content)
        return response.content
