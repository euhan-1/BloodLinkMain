import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def geocode_address(address: str) -> Optional[tuple[float, float]]:
    """Best-effort address -> (latitude, longitude) via Nominatim/OpenStreetMap
    (free, no API key). Returns None on no match, network failure, timeout,
    or a malformed response — never raises. This only ever prefills the
    registration form's map pin (see FacilityLocationFields on the frontend),
    which the applicant still confirms or drags into place by hand, the same
    required-confirmation gate CompleteProfile.tsx already uses; a geocoding
    hiccup must never block someone from filling out the form.

    countrycodes biases results to the Philippines, where every facility this
    app serves is located — a relevance hint, not a hard filter.

    Nominatim's usage policy requires a real identifying User-Agent (same
    reasoning as email_service.py's Resend calls, which hit an unrelated but
    similarly bot-signature-sensitive Cloudflare edge) and asks for roughly
    1 request/second at most, which registration traffic at this app's scale
    never approaches — no need for a client-side throttle here.
    """
    address = address.strip()
    if not address:
        return None

    params = urllib.parse.urlencode({"q": address, "format": "json", "limit": 1, "countrycodes": "ph"})
    req = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": "BloodLink-Server/1.0 (+https://github.com/euhan-1/bloodlink)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            results = json.loads(resp.read())
    except (urllib.error.URLError, ValueError):
        return None

    if not results:
        return None
    try:
        return float(results[0]["lat"]), float(results[0]["lon"])
    except (KeyError, TypeError, ValueError):
        return None
