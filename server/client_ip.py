"""Which address a request really came from, for rate limiting. Pure (no framework), so tests/test_client_ip.py can feed it headers.

Topology: browser -> Vercel (rewrite) -> Render's edge (Cloudflare) -> this app. What each hop does, and where it is documented:

  X-Forwarded-For   Render's docs do not describe it. A Render staff reply (feedback.render.com, 2021) says "we set the first IP in the
                    list to the real client IP", but a published header test against Render (github.com/johnbeynon/header-test) shows
                    the opposite for a spoofed value: Render KEEPS whatever the caller sent and APPENDS the address that connected to
                    it, then its own hops (`<caller's value>, <peer>, <Cloudflare>, <internal>`). So the leftmost entry is client
                    supplied and must never be used; this module reads no entry by position from the left.
  True-Client-IP    Set by Render's edge to the address that connected to it, and overwritten if the caller sends one (same test).
                    That makes it the one value in this app a caller cannot choose.

Behind Vercel the address that connects to Render is Vercel's, shared by every visitor, so keying a counter on it alone would let one
person's failed logins lock everybody out. Vercel overwrites X-Forwarded-For with the visitor's address and does not forward a
caller's (vercel.com/docs/headers/request-headers), so for a request that came through Vercel the entry just before the peer is the
visitor. This module finds the peer in the list by its value (True-Client-IP), not by counting hops, and takes the entry before it.

Limits of that, stated plainly: a caller who goes straight to the Render URL can choose that entry, so the per-address counter can be
dodged by a deliberate attacker. The per-account counter cannot. Nothing here is verified against production traffic (UNKNOWN until
you send a request through Vercel and one direct and look at the keys); the header facts above are from the sources named.

An address that is not a plain public IP (empty, garbage, private, loopback) is treated as "unknown" and returns None, so a proxy's
internal address can never become one shared key for every user.
"""
import ipaddress
from typing import Mapping, Optional


def _public(value: str) -> Optional[str]:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    return str(ip) if ip.is_global else None


def client_ip(headers: Mapping[str, str], peer_host: Optional[str] = None) -> Optional[str]:
    """The visitor's address as best the proxies can tell it, or None. `headers` must be case-insensitive (Starlette's are)."""
    peer = (headers.get("true-client-ip") or "").strip()
    forwarded = [p.strip() for p in (headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    if peer:
        if peer in forwarded:
            at = len(forwarded) - 1 - forwarded[::-1].index(peer)  # the last occurrence: the one Render's proxy appended
            if at > 0:
                return _public(forwarded[at - 1])
        return _public(peer)
    return _public(peer_host or "")
