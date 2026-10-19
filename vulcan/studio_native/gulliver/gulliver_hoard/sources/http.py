"""HTTP with a disk cache: elevation tiles, Overpass answers and place searches are kept under ``<data>/cache`` so a model
can be rebuilt with other settings without asking the servers again (and so the servers' usage policies are respected)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from .. import __version__
from ..errors import GulliverError
from ..hoard_link.atomic import write_bytes_atomic

USER_AGENT = f"GulliversHoard/{__version__} (printable map models; local app)"


class Fetcher:
    """GET/POST with an on-disk cache keyed by the request. ``transport`` (an ``httpx`` transport) is for the tests."""

    def __init__(self, cache_dir: Path, *, transport: Any = None, timeout: float = 60.0):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._transport = transport
        self._timeout = timeout
        self._lock = threading.Lock()
        self._client: Optional[httpx.Client] = None
        self.requests = 0          # network requests made (not cache hits), for the job log and the tests

    def client(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=self._timeout, follow_redirects=True,
                                            transport=self._transport)
            return self._client

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    # -- cache ------------------------------------------------------------------------------------------------------------
    def _path(self, bucket: str, key: str, ext: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / bucket / digest[:2] / f"{digest}{ext}"

    def cached(self, bucket: str, key: str, ext: str = ".bin", max_age_s: float | None = None) -> Optional[bytes]:
        path = self._path(bucket, key, ext)
        try:
            if max_age_s is not None and time.time() - path.stat().st_mtime > max_age_s:
                return None
            return path.read_bytes()
        except OSError:
            return None

    def store(self, bucket: str, key: str, data: bytes, ext: str = ".bin") -> None:
        path = self._path(bucket, key, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(path, data, fsync=False)

    def get(self, url: str, *, params: dict | None = None, bucket: str = "http", ext: str = ".bin", max_age_s: float | None = None,
            accept: Callable[[httpx.Response], bool] | None = None, use_cache: bool = True) -> bytes:
        key = url + "?" + json.dumps(params or {}, sort_keys=True)
        if use_cache:
            hit = self.cached(bucket, key, ext, max_age_s)
            if hit is not None:
                return hit
        try:
            self.requests += 1
            response = self.client().get(url, params=params)
        except httpx.HTTPError as exc:
            raise GulliverError("unreachable", f"Could not reach {httpx.URL(url).host}: {exc}", "Check the internet connection and try again.") from exc
        if response.status_code >= 400 or (accept and not accept(response)):
            raise GulliverError("source_failed", f"{httpx.URL(url).host} answered {response.status_code}.",
                                "The service may be busy; try again in a minute.", status_code=response.status_code)
        data = response.content
        if use_cache:
            self.store(bucket, key, data, ext)
        return data

    def post_form(self, url: str, data: dict[str, str], *, bucket: str, ext: str = ".json", max_age_s: float | None = None,
                  cache_key: str | None = None, timeout: float | None = None) -> bytes:
        key = cache_key or (url + "\n" + json.dumps(data, sort_keys=True))
        hit = self.cached(bucket, key, ext, max_age_s)
        if hit is not None:
            return hit
        # a whole-request deadline: some servers keep the connection alive with a trickle of bytes, so a read timeout
        # alone would never fire
        limit = timeout or self._timeout
        deadline = time.monotonic() + limit
        host = httpx.URL(url).host
        try:
            self.requests += 1
            with self.client().stream("POST", url, data=data, timeout=min(limit, 60.0)) as response:
                if response.status_code >= 400:
                    raise GulliverError("source_failed", f"{host} answered {response.status_code}.",
                                        "The service may be busy; try again in a minute.", status_code=response.status_code)
                chunks = []
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    if time.monotonic() > deadline:
                        raise GulliverError("source_failed", f"{host} took longer than {limit:g} s.", "The service may be busy.")
                return b"".join(chunks)
        except httpx.HTTPError as exc:
            raise GulliverError("unreachable", f"Could not reach {host}: {exc}", "Check the internet connection and try again.") from exc

    def cache_size(self) -> dict[str, Any]:
        total = 0
        files = 0
        buckets: dict[str, int] = {}
        for root, _dirs, names in os.walk(self.cache_dir):
            for name in names:
                try:
                    size = os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
                total += size
                files += 1
                bucket = Path(root).relative_to(self.cache_dir).parts[:1]
                if bucket:
                    buckets[bucket[0]] = buckets.get(bucket[0], 0) + size
        return {"bytes": total, "files": files, "buckets": buckets, "folder": str(self.cache_dir)}

    def clear(self, bucket: str = "") -> dict[str, Any]:
        removed = 0
        target = self.cache_dir / bucket if bucket else self.cache_dir
        for root, _dirs, names in os.walk(target, topdown=False):
            for name in names:
                try:
                    os.remove(os.path.join(root, name))
                    removed += 1
                except OSError:
                    pass
            if root != str(self.cache_dir):
                try:
                    os.rmdir(root)
                except OSError:
                    pass
        return {"removed": removed}
