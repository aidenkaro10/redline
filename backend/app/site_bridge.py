"""
Bridge to session C1's site hazard layer (backend/hazards/, backend/sites/),
used only when that code is merged in. Without it the server runs with one
demo site and demo-button hazards.

What it does:
  - loads the 79 Florida plant sites into the hub (GET /api/sites, worker sites)
  - makes the crew's site (CREW_SITE_ID, default eia-663 = Deerhaven, Gainesville)
    the default site for new workers
  - every REFRESH_S seconds: takes C1's latest hazard picture and
      * pushes each SiteStatus to the dashboard (hub.set_site_status)
      * turns band patterns on/off per site (hub.set_site_hazard): C1's
        "BUZZ:LIGHTNING" -> lightning, "BUZZ:FLOOD_FIRE" -> flood.
        C1's site-level HEAT_YELLOW is not sent to bands: each worker's own
        heat alert (from their core temp) owns the heat patterns.

C1 suggests patterns; the alert router here decides what a band plays.
"""

import asyncio
import logging
import os

log = logging.getLogger(__name__)

REFRESH_S = 15                     # how often we read C1's cached picture (it rebuilds itself every 60 s)
PATTERN_KIND = {"BUZZ:LIGHTNING": "lightning", "BUZZ:FLOOD_FIRE": "flood"}


class SiteBridge:
    def __init__(self, hub):
        # ImportError here simply means C1 isn't merged in yet.
        from backend.hazards import contract, picture
        self.contract, self.picture = contract, picture
        self.hub = hub
        self.ours: dict[str, set[str]] = {}          # site -> hazard kinds this bridge turned on
        self.crew_site = os.getenv("CREW_SITE_ID", "eia-663")

    def load_sites(self) -> int:
        old_default = self.hub.default_site_id
        for rec in self.contract.site_records():
            self.hub.add_site({**rec, "site_id": rec["id"]})
        if self.crew_site in self.hub.sites:
            self.hub.default_site_id = self.crew_site
            # The built-in demo site is only a stand-in for C1's real ones: drop it if nobody's on it.
            if old_default != self.crew_site and not any(
                    w["site_id"] == old_default for w in self.hub.workers.values()):
                self.hub.sites.pop(old_default, None)
        return len(self.hub.sites)

    def refresh(self) -> None:
        pic = self.picture.latest_picture()
        if pic is None:
            return                                   # first build still running (~10-15 s)
        for status in self.contract.site_statuses(pic):
            sid = status["site_id"]
            if sid in self.hub.sites and self.hub.site_status.get(sid) != status:
                self.hub.set_site_status(sid, status)
        wanted: dict[str, set[str]] = {}
        for sid, line in self.picture.band_commands(pic).items():
            kind = PATTERN_KIND.get(line)
            if kind:
                wanted.setdefault(sid, set()).add(kind)
        for sid in set(wanted) | set(self.ours):
            now_on, was_on = wanted.get(sid, set()), self.ours.get(sid, set())
            for kind in now_on - was_on:
                self.hub.set_site_hazard(sid, kind, True, source="C1 hazard feeds")
            for kind in was_on - now_on:
                self.hub.set_site_hazard(sid, kind, False, source="C1 hazard feeds")
            self.ours[sid] = now_on

    def test_alert(self, pattern: str, site_id: str | None) -> None:
        """Demo button: C1 injects a fake event so its real rules (10-mile, 30-min countdown) run."""
        self.contract.test_alert(pattern, site_id or self.hub.default_site_id)

    def clear_test_hazards(self) -> None:
        self.contract.clear_test_hazards()

    async def rebuild_now(self) -> None:
        """Rebuild C1's picture right away (on a thread) and refresh, so a demo button shows on the
        site card at once instead of after C1's next 60 s rebuild + our 15 s poll."""
        await asyncio.to_thread(self.picture.hazard_picture)
        self.refresh()

    async def run(self) -> None:
        self.picture.start_background_refresh(60)
        while True:
            try:
                self.refresh()
            except Exception:
                log.exception("site hazard refresh failed")
            await asyncio.sleep(REFRESH_S)


def make_bridge(hub, mode: str = "fake") -> "SiteBridge | None":
    """
    mode: "fake" = C1's built-in demo data for every feed (no internet, good for replay demos),
          "live" = real NWS / GOES-19 / NWPS / FIRMS feeds, "off" = don't use C1 at all.
    """
    if mode == "off":
        log.info("Site hazard layer switched off (--hazards off): demo-button hazards only")
        return None
    try:
        bridge = SiteBridge(hub)
    except ImportError:
        log.info("Site hazard layer (session C1) not merged yet: one demo site, demo-button hazards")
        return None
    try:
        from backend.hazards import store
        store.set_mode(mode)
    except Exception as e:
        log.warning("Could not set hazard mode %s (%s)", mode, e)
    n = bridge.load_sites()
    log.info("Site hazard layer wired (%s data): %d sites, crew site %s", mode, n, hub.default_site_id)
    return bridge
