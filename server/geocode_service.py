import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from typing import Callable, Optional

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy (operations.osmfoundation.org/policies/nominatim) asks for: an identifying User-Agent (not a library
# default) that says who is calling and how to reach them, at most one request per second, and no bulk or repeated identical
# queries. This module does all three: the agent below, a process-wide throttle, and a short cache. The agent names the project's
# public repository; set NOMINATIM_USER_AGENT to add a contact address of your own.
DEFAULT_USER_AGENT = "BloodLink/1.0 (thesis project; +https://github.com/euhan-1/BloodLinkMain)"

MIN_SECONDS_BETWEEN_CALLS = 1.0
# A caller never waits longer than this for its turn; past it the lookup is skipped and the applicant places the pin by hand.
MAX_WAIT_FOR_TURN_SECONDS = 4.0
CACHE_SECONDS = 300
CACHE_MAX_ENTRIES = 256


def user_agent() -> str:
    return os.environ.get("NOMINATIM_USER_AGENT", "").strip() or DEFAULT_USER_AGENT


class _OutgoingThrottle:
    """At most one outgoing call per `gap` seconds across the whole process: each caller reserves the next free slot and sleeps until
    it. A caller whose slot is more than `max_wait` away is refused instead of queued, so a burst cannot pile up threads."""

    def __init__(self, gap: float, max_wait: float, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.gap, self.max_wait, self.clock, self.sleep = gap, max_wait, clock, sleep
        self._next_slot = 0.0
        self._lock = threading.Lock()

    def wait_for_turn(self) -> bool:
        with self._lock:
            now = self.clock()
            slot = max(now, self._next_slot)
            if slot - now > self.max_wait:
                return False
            self._next_slot = slot + self.gap
        if slot > now:
            self.sleep(slot - now)
        return True


class _Cache:
    """Identical queries inside `ttl` seconds are answered from memory (a miss from Nominatim is remembered too). Bounded, oldest out."""

    def __init__(self, ttl: float, max_entries: int, clock: Callable[[], float] = time.monotonic):
        self.ttl, self.max_entries, self.clock = ttl, max_entries, clock
        self._items: "OrderedDict[str, tuple[float, Optional[tuple[float, float]]]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str):
        """(hit, value). A miss is (False, None)."""
        with self._lock:
            entry = self._items.get(key)
            if entry is None or self.clock() - entry[0] >= self.ttl:
                self._items.pop(key, None)
                return False, None
            return True, entry[1]

    def put(self, key: str, value: Optional[tuple[float, float]]) -> None:
        with self._lock:
            self._items[key] = (self.clock(), value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_throttle = _OutgoingThrottle(MIN_SECONDS_BETWEEN_CALLS, MAX_WAIT_FOR_TURN_SECONDS)
_cache = _Cache(CACHE_SECONDS, CACHE_MAX_ENTRIES)


def normalize(address: str) -> str:
    return " ".join(address.lower().split())


def geocode_address(address: str) -> Optional[tuple[float, float]]:
    """Best-effort address -> (latitude, longitude) via Nominatim/OpenStreetMap
    (free, no API key). Returns None on no match, network failure, timeout,
    a malformed response, or when this process has used its one call per second and
    cannot give this caller a turn soon — never raises. This only ever prefills the
    registration form's map pin (see FacilityLocationFields on the frontend),
    which the applicant still confirms or drags into place by hand, the same
    required-confirmation gate CompleteProfile.tsx already has; a geocoding
    hiccup must never block someone from filling out the form.

    countrycodes limits results to the Philippines, where every facility this
    app serves is located — Nominatim excludes matches outside the listed
    countries, so an address that only exists elsewhere returns no match.

    The endpoint is public (registration cannot require a login), so what protects Nominatim from being used through us is, in
    order: the per-address limit and the minimum length in main.py, the cache here (a repeated query costs nothing), and the
    process-wide one-call-per-second throttle here.
    """
    key = normalize(address)
    if not key:
        return None
    hit, cached = _cache.get(key)
    if hit:
        return cached
    if not _throttle.wait_for_turn():
        return None  # busy: not cached, so the next try can still succeed

    params = urllib.parse.urlencode({"q": address.strip(), "format": "json", "limit": 1, "countrycodes": "ph"})
    req = urllib.request.Request(f"{NOMINATIM_URL}?{params}", headers={"User-Agent": user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            results = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None  # a failure is not cached: it may be a blip

    result: Optional[tuple[float, float]] = None
    if results:
        try:
            result = float(results[0]["lat"]), float(results[0]["lon"])
        except (KeyError, TypeError, ValueError):
            return None
    _cache.put(key, result)
    return result
