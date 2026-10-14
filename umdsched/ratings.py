"""Professor ratings from PlanetTerp (planetterp.com), cached for a week."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API_URL = "https://planetterp.com/api/v1/professor"
TTL_SECONDS = 7 * 24 * 3600


class RatingCache:
    def __init__(self, cache_dir: Path, *, offline: bool = False):
        self.path = Path(cache_dir) / "planetterp-ratings.json"
        self.offline = offline
        try:
            self.data: dict = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def _fresh(self, name: str) -> bool:
        entry = self.data.get(name)
        return bool(entry) and (self.offline or time.time() - entry["t"] < TTL_SECONDS)

    def _fetch(self, name: str) -> float | None:
        import requests

        try:
            resp = requests.get(API_URL, params={"name": name}, timeout=15)
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        rating = resp.json().get("average_rating")
        return float(rating) if rating is not None else None

    def get_many(self, names: list[str]) -> dict[str, float | None]:
        names = sorted(set(n for n in names if n))
        missing = [n for n in names if not self._fresh(n)]
        if missing and not self.offline:
            with ThreadPoolExecutor(max_workers=6) as pool:
                for name, rating in zip(missing, pool.map(self._fetch, missing)):
                    self.data[name] = {"r": rating, "t": time.time()}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data))
            except OSError:
                pass
        return {n: (self.data.get(n) or {}).get("r") for n in names}
