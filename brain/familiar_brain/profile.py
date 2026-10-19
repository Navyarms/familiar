"""What Familiar knows about the user outside of conversation memory: name and location.

Location comes from settings (user-entered) or, when allowed, from a network address lookup. The lookup sends
nothing but the request itself; the result is a city-level guess that can be off (VPNs, mobile carriers), which
is why the prompt labels it approximate.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

LOOKUPS = (
    ("https://ipwho.is/", lambda j: j.get("success", True) and (j.get("city"), j.get("region"), j.get("country"))),
    ("http://ip-api.com/json/?fields=status,city,regionName,country", lambda j: j.get("status") == "success" and (j.get("city"), j.get("regionName"), j.get("country"))),
)


async def detect_location(timeout: float = 4.0) -> str | None:
    """'City, Region, Country' from the public address, or None when offline / blocked."""
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "Familiar/0.1 (local assistant)"}) as c:
        for url, pick in LOOKUPS:
            try:
                r = await c.get(url)
                r.raise_for_status()
                parts = pick(r.json())
                if parts:
                    label = ", ".join(p for p in parts if p)
                    if label:
                        return label
            except Exception as e:  # noqa: BLE001 - any failure just means try the next provider
                log.info("location lookup via %s failed: %s", url.split("/")[2], e)
    return None


def location_of(settings) -> tuple[str | None, str]:
    """(label, source) where source is 'settings', 'network' or 'none'."""
    if settings.user_location and settings.user_location.strip():
        return settings.user_location.strip(), "settings"
    if settings.location_detect and settings.location_detected:
        return settings.location_detected, "network"
    return None, "none"
