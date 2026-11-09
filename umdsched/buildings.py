"""Campus building locations and walking-time estimates.

The built-in table holds *approximate* coordinates for common College Park
teaching buildings (good to roughly a block). Run `umd-sched buildings
--refresh` to replace it with exact coordinates from umd.io's campus map,
or point --buildings at your own JSON file ({"IRB": [lat, lon], ...}).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from umdsched.models import ONLINE

# code: (name, lat, lon)
BUILTIN_BUILDINGS: dict[str, tuple[str, float, float]] = {
    "AJC": ("A. James Clark Hall", 38.9917, -76.9396),
    "ANS": ("Animal Sciences/Agricultural Engineering", 38.9915, -76.9437),
    "ARC": ("Architecture Building", 38.9840, -76.9475),
    "ARM": ("Armory", 38.9861, -76.9388),
    "ASY": ("Art-Sociology Building", 38.9854, -76.9479),
    "ATL": ("Atlantic Building", 38.9906, -76.9408),
    "AVW": ("A.V. Williams Building", 38.9908, -76.9365),
    "BPS": ("Biology-Psychology Building", 38.9887, -76.9428),
    "BRB": ("Bioscience Research Building", 38.9889, -76.9437),
    "CCC": ("Cambridge Community Center", 38.9921, -76.9433),
    "CHE": ("Chemical and Nuclear Engineering Building", 38.9907, -76.9393),
    "CHM": ("Chemistry Building", 38.9895, -76.9405),
    "COL": ("Cole Student Activities Building", 38.9880, -76.9468),
    "CSI": ("Computer Science Instructional Center", 38.9900, -76.9362),
    "CSS": ("Computer and Space Sciences Building", 38.9907, -76.9425),
    "EDU": ("Benjamin Building", 38.9867, -76.9477),
    "EGL": ("Engineering Laboratory Building", 38.9893, -76.9374),
    "EGR": ("Glenn L. Martin Hall", 38.9889, -76.9376),
    "ERF": ("Energy Research Facility", 38.9915, -76.9372),
    "ESJ": ("Edward St. John Learning and Teaching Center", 38.9871, -76.9418),
    "GEO": ("Geology Building", 38.9881, -76.9405),
    "HBK": ("Hornbake Library", 38.9882, -76.9417),
    "HJP": ("H.J. Patterson Hall", 38.9873, -76.9431),
    "HZF": ("Holzapfel Hall", 38.9864, -76.9405),
    "IRB": ("Brendan Iribe Center", 38.9890, -76.9364),
    "ITV": ("Instructional Television Facility", 38.9899, -76.9380),
    "JMP": ("J.M. Patterson Building", 38.9904, -76.9398),
    "JMZ": ("Jimenez Hall", 38.9868, -76.9453),
    "KEB": ("Jeong H. Kim Engineering Building", 38.9908, -76.9382),
    "KEY": ("Francis Scott Key Hall", 38.9851, -76.9426),
    "KNI": ("Knight Hall", 38.9845, -76.9476),
    "LEF": ("LeFrak Hall", 38.9834, -76.9437),
    "MCK": ("McKeldin Library", 38.9860, -76.9450),
    "MMH": ("Marie Mount Hall", 38.9848, -76.9408),
    "MOR": ("Morrill Hall", 38.9845, -76.9447),
    "MTH": ("Kirwan Hall (Mathematics)", 38.9884, -76.9391),
    "PAC": ("Clarice Smith Performing Arts Center", 38.9906, -76.9504),
    "PHY": ("John S. Toll Physics Building", 38.9887, -76.9400),
    "PLS": ("Plant Sciences Building", 38.9887, -76.9414),
    "RIT": ("Ritchie Coliseum", 38.9843, -76.9348),
    "SHM": ("Shoemaker Building", 38.9842, -76.9426),
    "SKN": ("Skinner Building", 38.9845, -76.9419),
    "SPH": ("School of Public Health", 38.9935, -76.9431),
    "SQH": ("Susquehanna Hall", 38.9825, -76.9435),
    "SSU": ("Adele H. Stamp Student Union", 38.9882, -76.9446),
    "SYM": ("Symons Hall", 38.9870, -76.9403),
    "TLF": ("Taliaferro Hall", 38.9850, -76.9433),
    "TWS": ("Tawes Hall", 38.9859, -76.9485),
    "TYD": ("Tydings Hall", 38.9849, -76.9440),
    "VMH": ("Van Munching Hall", 38.9832, -76.9469),
    "WDS": ("Woods Hall", 38.9847, -76.9416),
    "XFC": ("Xfinity Center", 38.9955, -76.9417),
}

UMDIO_BUILDINGS_URL = "https://api.umd.io/v1/map/buildings"


@dataclass
class WalkModel:
    speed_m_per_min: float = 80.0  # ~3 mph, a typical walking pace
    path_factor: float = 1.3  # campus paths are not straight lines
    overhead_min: float = 1.0  # leaving one building, finding the room in the next
    unknown_min: float = 8.0  # assumed walk when a building isn't in the table

    def __post_init__(self):
        self.buildings: dict[str, tuple[str, float, float]] = dict(BUILTIN_BUILDINGS)

    def load_overrides(self, path: Path) -> int:
        """Merge buildings from a JSON file; returns how many were loaded.

        Accepts {"CODE": [lat, lon]}, {"CODE": {"name":..,"lat":..,"lon":..}},
        or a umd.io-style list of {"code","name","lat","long"} objects.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        count = 0
        items = data.items() if isinstance(data, dict) else ((d.get("code"), d) for d in data)
        for code, val in items:
            if not code:
                continue
            code = str(code).upper()
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                name, lat, lon = self.buildings.get(code, (code, 0, 0))[0], val[0], val[1]
            elif isinstance(val, dict):
                lat = val.get("lat", val.get("latitude"))
                lon = val.get("lon", val.get("lng", val.get("long", val.get("longitude"))))
                name = val.get("name") or code
            else:
                continue
            try:
                self.buildings[code] = (name, float(lat), float(lon))
                count += 1
            except (TypeError, ValueError):
                continue
        return count

    def known(self, code: str | None) -> bool:
        return bool(code) and code.upper() in self.buildings

    def distance_m(self, a: str, b: str) -> float | None:
        a, b = a.upper(), b.upper()
        if a not in self.buildings or b not in self.buildings:
            return None
        _, lat1, lon1 = self.buildings[a]
        _, lat2, lon2 = self.buildings[b]
        r = 6371000.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp, dl = p2 - p1, math.radians(lon2 - lon1)
        h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(h))

    def minutes(self, a: str | None, b: str | None) -> float:
        """Estimated walking minutes between two building codes.

        Same building, online, or TBA locations count as no walk.
        """
        if not a or not b:
            return 0.0
        a, b = a.upper(), b.upper()
        if a == b or ONLINE in (a, b):
            return 0.0
        dist = self.distance_m(a, b)
        if dist is None:
            return self.unknown_min
        return dist * self.path_factor / self.speed_m_per_min + self.overhead_min


def fetch_umdio_buildings(session=None) -> list[dict]:
    import requests

    sess = session or requests.Session()
    resp = sess.get(UMDIO_BUILDINGS_URL, timeout=20)
    resp.raise_for_status()
    out = []
    for b in resp.json():
        code = (b.get("code") or "").strip().upper()
        lat = b.get("lat", b.get("latitude"))
        lon = b.get("long", b.get("lng", b.get("lon")))
        if code and lat not in (None, "") and lon not in (None, ""):
            out.append({"code": code, "name": b.get("name") or code, "lat": float(lat), "lon": float(lon)})
    return out
