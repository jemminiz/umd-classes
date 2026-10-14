"""Course data loading: live sources, an on-disk cache, and JSON data files."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from umdsched.models import Course


class DataError(Exception):
    pass


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "umd-sched"


def default_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "umd-sched"


def load_data_file(path: Path) -> dict[str, Course]:
    data = json.loads(Path(path).read_text())
    courses = data["courses"] if isinstance(data, dict) else data
    return {c.course_id: c for c in (Course.from_dict(d) for d in courses)}


def save_data_file(path: Path, term: str, courses: list[Course], source: str) -> None:
    payload = {
        "term": term,
        "source": source,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "courses": [c.to_dict() for c in courses],
    }
    Path(path).write_text(json.dumps(payload, indent=1))


class CourseLoader:
    """Fetches courses from Testudo (or umd.io) with a small JSON cache.

    Seat counts change constantly during registration, so the default cache
    lifetime is short; --offline uses whatever is cached regardless of age.
    """

    def __init__(
        self,
        term: str,
        *,
        source: str = "testudo",
        cache_dir: Path | None = None,
        ttl_minutes: float = 30,
        refresh: bool = False,
        offline: bool = False,
        data_files: list[Path] | None = None,
    ):
        self.term = term
        self.source = source
        self.cache_dir = (cache_dir or default_cache_dir()) / term
        self.ttl = ttl_minutes * 60
        self.refresh = refresh
        self.offline = offline
        self.preloaded: dict[str, Course] = {}
        for f in data_files or []:
            self.preloaded.update(load_data_file(f))

    def _cache_path(self, cid: str) -> Path:
        return self.cache_dir / f"{self.source}-{cid}.json"

    def _read_cache(self, cid: str, *, ignore_age: bool) -> Course | None:
        path = self._cache_path(cid)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not ignore_age and time.time() - data.get("fetched_at", 0) > self.ttl:
            return None
        return Course.from_dict(data["course"]) if data.get("course") else None

    def _write_cache(self, cid: str, course: Course) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(cid).write_text(json.dumps({"fetched_at": time.time(), "course": course.to_dict()}))
        except OSError:
            pass

    def _fetch(self, cid: str) -> Course | None:
        import requests

        from umdsched.sources import testudo, umdio

        module = umdio if self.source == "umdio" else testudo
        session = requests.Session()
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                return module.fetch_course(cid, self.term, session=session)
            except requests.RequestException as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        if isinstance(last_err, requests.HTTPError) and last_err.response is not None:
            why = f"HTTP {last_err.response.status_code}"
        elif isinstance(last_err, (requests.ConnectionError, requests.Timeout)):
            why = "couldn't connect"
        else:
            why = type(last_err).__name__
        raise DataError(
            f"couldn't fetch {cid} from {self.source} ({why}). Check your connection, "
            f"try --source umdio, or use --offline / --data FILE."
        )

    def load_one(self, cid: str) -> Course:
        cid = cid.upper()
        if cid in self.preloaded:
            return self.preloaded[cid]
        if not self.refresh:
            cached = self._read_cache(cid, ignore_age=self.offline)
            if cached:
                return cached
        if self.offline:
            raise DataError(f"{cid} is not cached and --offline was given")
        try:
            course = self._fetch(cid)
        except DataError:
            stale = self._read_cache(cid, ignore_age=True)
            if stale:
                return stale
            raise
        if course is None:
            raise DataError(f"{cid} isn't offered in term {self.term} (or doesn't exist)")
        self._write_cache(cid, course)
        return course

    def load(self, course_ids: list[str]) -> dict[str, Course]:
        ids = list(dict.fromkeys(c.upper() for c in course_ids))
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self.load_one, ids))
        return dict(zip(ids, results))
