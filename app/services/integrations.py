"""External integrations: NPMRDS, RITIS, Waze CCP, WZDx.

Each client is a thin async HTTP wrapper returning parsed payloads that the
Celery tasks persist to travel_time_records / ritis_events / waze_jam_records
/ wzdx_work_zones.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from app.core.config import settings

_TIMEOUT = aiohttp.ClientTimeout(total=30)


class NPMRDSClient:
    """NPMRDS TMC-level 15-min epoch travel time ingestion."""

    def __init__(self, api_key: Optional[str] = None, endpoint: Optional[str] = None):
        self.api_key = api_key or settings.NPMRDS_API_KEY
        self.endpoint = (endpoint or settings.NPMRDS_API_ENDPOINT).rstrip("/")

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    async def fetch_travel_times(
        self, tmc_codes: List[str], start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        if not self.api_key:
            return []
        url = f"{self.endpoint}/travel-times"
        params = {
            "tmc": ",".join(tmc_codes),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "epoch": "15min",
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=self._headers(), params=params, timeout=_TIMEOUT) as r:
                    if r.status != 200:
                        return []
                    data = await r.json()
                    return data.get("records", data.get("value", []))
        except Exception:
            return []


class RITISClient:
    """RITIS bidirectional sync: pull active events + push detected anomalies."""

    def __init__(self, api_key: Optional[str] = None, endpoint: Optional[str] = None):
        self.api_key = api_key or settings.RITIS_API_KEY
        self.endpoint = (endpoint or settings.RITIS_API_ENDPOINT).rstrip("/")

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    async def pull_active_events(self) -> List[Dict[str, Any]]:
        if not self.api_key:
            return []
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{self.endpoint}/events/active", headers=self._headers(), timeout=_TIMEOUT
                ) as r:
                    if r.status != 200:
                        return []
                    data = await r.json()
                    return data.get("events", data.get("value", []))
        except Exception:
            return []

    async def push_incident(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_key:
            return {"status": "skipped", "reason": "no_api_key"}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{self.endpoint}/events", headers=self._headers(),
                    json=incident, timeout=_TIMEOUT,
                ) as r:
                    body: Any = {}
                    try:
                        body = await r.json()
                    except Exception:
                        pass
                    return {"status": "ok" if r.status in (200, 201) else f"http_{r.status}", "body": body}
        except Exception as e:
            return {"status": "error", "reason": str(e)}


class WazeClient:
    """Waze CCP (EDC-5) jam feed poller."""

    def __init__(self, feed_url: Optional[str] = None):
        self.feed_url = feed_url or settings.WAZE_CCP_FEED_URL

    async def poll_jams(self) -> List[Dict[str, Any]]:
        if not self.feed_url:
            return []
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(self.feed_url, timeout=_TIMEOUT) as r:
                    if r.status != 200:
                        return []
                    data = await r.json()
                    return data.get("jams", data.get("value", []))
        except Exception:
            return []


class WZDXClient:
    """WZDx (EDC-5) state DOT work zone feed sync (v4.0)."""

    def __init__(self, feed_urls: Optional[str] = None):
        self.feed_urls = feed_urls or settings.WZDX_FEED_URLS

    def _parse_feeds(self) -> List[Dict[str, str]]:
        feeds: List[Dict[str, str]] = []
        for part in (self.feed_urls or "").split(","):
            part = part.strip()
            if not part or "|" not in part:
                continue
            state, url = part.split("|", 1)
            feeds.append({"state": state.strip(), "url": url.strip()})
        return feeds

    async def sync_feeds(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for feed in self._parse_feeds():
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(feed["url"], timeout=_TIMEOUT) as r:
                        if r.status != 200:
                            continue
                        data = await r.json()
                        features = data.get("features", [])
                        for fz in features:
                            props = fz.get("properties", {})
                            out.append({
                                "feed_id": fz.get("id", ""),
                                "state": feed["state"],
                                "road_name": props.get("road_name", props.get("roadName", "")),
                                "geometry": fz.get("geometry"),
                                "start_date": props.get("start_date"),
                                "end_date": props.get("end_date"),
                                "is_active": True,
                            })
            except Exception:
                continue
        return out
