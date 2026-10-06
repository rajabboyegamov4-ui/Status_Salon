"""Small in-memory limits per client address (one app process; enough for one Railway instance)."""

import time
from collections import defaultdict, deque

from fastapi import Request


def client_ip(request: Request) -> str:
    # Behind Railway's proxy the real address is the last one the proxy appended.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "?"


class Window:
    """At most `limit` events per `seconds` for each key."""

    def __init__(self, limit: int, seconds: int):
        self.limit, self.seconds = limit, seconds
        self.events: dict[str, deque] = defaultdict(deque)

    def _trim(self, key: str) -> deque:
        q, cutoff = self.events[key], time.monotonic() - self.seconds
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        return len(self._trim(key)) >= self.limit

    def hit(self, key: str) -> None:
        self._trim(key).append(time.monotonic())
        if len(self.events) > 5000:  # forget addresses that went quiet, so memory stays flat
            for k in [k for k in self.events if not self._trim(k)]:
                del self.events[k]

    def clear(self, key: str) -> None:
        self.events.pop(key, None)
