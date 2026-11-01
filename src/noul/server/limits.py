"""Abuse and cost limits for the public HTTP connector: per-client rate limiting and client-IP resolution.

The public connector stores no user data, so the risks are upstream API quotas (OpenAlex, Semantic Scholar, NCBI)
and hosting cost. These limits are deliberately simple and in-process (one Cloud Run instance = one bucket table);
`--max-instances` bounds the total."""

from __future__ import annotations

import ipaddress
import os
import threading
import time
from collections import OrderedDict
from typing import Any


class RateLimiter:
    """Token bucket per key (client IP): `rate` requests per minute with bursts up to `burst`.

    The table is bounded (LRU) so spoofed or rotating keys cannot grow memory without limit."""

    def __init__(self, rate_per_minute: float, burst: int, max_keys: int = 10_000, clock: Any = time.monotonic):
        self.rate = max(rate_per_minute, 0.0) / 60.0  # tokens per second
        self.burst = max(int(burst), 1)
        self.max_keys = max_keys
        self.clock = clock
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()  # key -> (tokens, last)

    def check(self, key: str) -> float:
        """0.0 when the request may proceed, else the seconds to wait before retrying."""
        now = self.clock()
        with self._lock:
            tokens, last = self._buckets.pop(key, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * self.rate)
            if tokens >= 1.0:
                wait, tokens = 0.0, tokens - 1.0
            else:
                wait = (1.0 - tokens) / self.rate if self.rate else 60.0
            self._buckets[key] = (tokens, now)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
        return wait


def client_key(ip: str) -> str:
    """Rate-limit key: IPv6 addresses are grouped by /64 (one subscriber usually owns a whole /64)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def client_ip(scope: dict[str, Any], headers: dict[str, str], trusted_proxy_hops: int) -> str:
    """The client address. With `trusted_proxy_hops` = n > 0, the n-th X-Forwarded-For entry from the right is used
    (entries to its left are client-supplied and can be forged); otherwise the TCP peer address."""
    if trusted_proxy_hops > 0:
        hops = [h.strip() for h in headers.get("x-forwarded-for", "").split(",") if h.strip()]
        if len(hops) >= trusted_proxy_hops:
            candidate = hops[-trusted_proxy_hops]
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
    peer = scope.get("client") or ("unknown", 0)
    return str(peer[0])


def env_rate_limiter(public: bool) -> RateLimiter | None:
    """Per-client flood guard from NOUL_RATE_LIMIT (requests/minute, 0 disables) and NOUL_RATE_BURST; on by
    default in public mode (600/min, burst 120 - hosted clients share egress IPs), off otherwise."""
    raw = os.environ.get("NOUL_RATE_LIMIT")
    if raw is None and not public:
        return None
    rate = float(raw) if raw not in (None, "") else 600.0
    if rate <= 0:
        return None
    return RateLimiter(rate, int(os.environ.get("NOUL_RATE_BURST") or 120))


def env_tool_rate_limiter(public: bool) -> RateLimiter | None:
    """Per-instance ceiling on tool calls (NOUL_TOOL_RATE_LIMIT per minute, NOUL_TOOL_RATE_BURST; public default
    300/min, burst 60): the cost control for upstream APIs, whatever client addresses are used."""
    raw = os.environ.get("NOUL_TOOL_RATE_LIMIT")
    if raw is None and not public:
        return None
    rate = float(raw) if raw not in (None, "") else 300.0
    if rate <= 0:
        return None
    return RateLimiter(rate, int(os.environ.get("NOUL_TOOL_RATE_BURST") or 60), max_keys=1)


class Concurrency:
    """Shared cap on tool calls running at once (MCP and REST together)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._lock = threading.Lock()
        self._active = 0

    def try_acquire(self) -> bool:
        with self._lock:
            if self._active >= self.limit:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)


def env_trusted_proxy_hops() -> int:
    """NOUL_TRUSTED_PROXY: how many proxies append to X-Forwarded-For (Cloud Run: 1). 0 = ignore the header."""
    try:
        return max(int(os.environ.get("NOUL_TRUSTED_PROXY") or 0), 0)
    except ValueError:
        return 0


def env_max_concurrency(public: bool) -> int | None:
    """NOUL_MAX_CONCURRENCY: tool calls running at once (default 8 in public mode, unlimited otherwise)."""
    raw = os.environ.get("NOUL_MAX_CONCURRENCY")
    if raw in (None, ""):
        return 8 if public else None
    value = int(raw)
    return value if value > 0 else None
