import datetime
import json
import math
import os
import time
import urllib.parse
import urllib.request


DEFAULT_OVERPASS_URLS = "https://overpass-api.de/api/interpreter,https://overpass.openstreetmap.fr/api/interpreter"
OVERPASS_URLS = [
    url.strip()
    for url in os.getenv("SURFACE_OVERPASS_URLS", os.getenv("SURFACE_OVERPASS_URL", DEFAULT_OVERPASS_URLS)).split(",")
    if url.strip()
]
OVERPASS_TIMEOUT_SECS = int(os.getenv("SURFACE_OVERPASS_TIMEOUT_SECS", "60"))
SURFACE_MATCH_RADIUS_M = float(os.getenv("SURFACE_MATCH_RADIUS_M", "50"))
SURFACE_BBOX_MARGIN_DEGREES = float(os.getenv("SURFACE_BBOX_MARGIN_DEGREES", "0.002"))
SURFACE_OVERPASS_RETRIES = int(os.getenv("SURFACE_OVERPASS_RETRIES", "2"))
SURFACE_OVERPASS_RETRY_WAIT_SECS = float(os.getenv("SURFACE_OVERPASS_RETRY_WAIT_SECS", "2"))
SURFACE_SOURCE = "overpass-osm"

SURFACE_GROUPS = ["asphalt", "concrete", "paved_other", "gravel", "trail", "unknown"]
HARD_SURFACES = {"asphalt", "concrete", "paved_other"}
SOFT_SURFACES = {"gravel", "trail"}

ASPHALT_VALUES = {"asphalt"}
CONCRETE_VALUES = {"concrete", "concrete:lanes", "concrete:plates"}
PAVED_VALUES = {"paved", "paving_stones", "sett", "cobblestone", "bricks", "metal"}
GRAVEL_VALUES = {"gravel", "fine_gravel", "compacted", "pebblestone"}
TRAIL_VALUES = {"unpaved", "ground", "dirt", "earth", "grass", "sand", "mud", "woodchips", "snow"}
PAVED_HIGHWAYS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "residential",
    "living_street", "service", "unclassified", "road",
}
TRAIL_HIGHWAYS = {"path", "footway", "bridleway", "steps", "pedestrian"}


def surface_sync_enabled():
    return os.getenv("SURFACE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def iso_utc(ts_ms):
    return datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.UTC).replace(microsecond=0).isoformat()


def analyze_route_surfaces(points, interval_secs):
    if not surface_sync_enabled() or len(points) < 2:
        return [], empty_summary(None)

    route_segments = downsample_route_segments(points, interval_secs)
    if not route_segments:
        return [], empty_summary(SURFACE_SOURCE)

    way_points = [route_segments[0]["start"], *[segment["end"] for segment in route_segments]]
    ways = fetch_overpass_ways(way_points)
    segments = []
    totals = {group: 0.0 for group in SURFACE_GROUPS}
    for sample_order, route_segment in enumerate(route_segments, start=1):
        start = route_segment["start"]
        end = route_segment["end"]
        distance_km = route_segment["distance_km"]
        if distance_km <= 0:
            continue
        way, match_distance_m = nearest_way(start, end, ways)
        tags = way.get("tags", {}) if way else {}
        surface, confidence = classify_surface(tags, match_distance_m)
        totals[surface] += distance_km
        segments.append({
            "sample_order": sample_order,
            "start_time_utc": iso_utc(start[0]),
            "end_time_utc": iso_utc(end[0]),
            "distance_km": round(distance_km, 4),
            "surface": surface,
            "surface_source": SURFACE_SOURCE,
            "surface_confidence": confidence,
            "raw_surface": tags.get("surface"),
            "raw_highway": tags.get("highway"),
            "raw_tracktype": tags.get("tracktype"),
            "match_distance_m": round(match_distance_m, 1) if match_distance_m is not None else None,
            "tags_json": json.dumps(tags, sort_keys=True) if tags else None,
        })
    return segments, summarize_totals(totals, SURFACE_SOURCE)


def downsample_route_segments(points, interval_secs):
    if len(points) < 2:
        return []
    interval_ms = int(interval_secs * 1000)
    bucket_start = points[0]
    previous = points[0]
    next_ts = bucket_start[0] + interval_ms
    distance_km = 0.0
    segments = []
    for point in points[1:]:
        distance_km += haversine_km(previous[1], previous[2], point[1], point[2])
        if point[0] >= next_ts:
            segments.append({"start": bucket_start, "end": point, "distance_km": distance_km})
            bucket_start = point
            distance_km = 0.0
            next_ts = point[0] + interval_ms
        previous = point
    if distance_km > 0 and bucket_start != points[-1]:
        segments.append({"start": bucket_start, "end": points[-1], "distance_km": distance_km})
    return segments


def fetch_overpass_ways(points):
    ways = {}
    south, west, north, east = bbox(points, SURFACE_BBOX_MARGIN_DEGREES)
    queries = [
        f"way[\"highway\"][\"surface\"]({south:.6f},{west:.6f},{north:.6f},{east:.6f});",
        f"way[\"highway\"][\"tracktype\"]({south:.6f},{west:.6f},{north:.6f},{east:.6f});",
        (
            f"way[\"highway\"~\"^(path|footway|track|bridleway|steps|pedestrian)$\"]"
            f"({south:.6f},{west:.6f},{north:.6f},{east:.6f});"
        ),
        (
            f"way[\"highway\"~\"^(residential|living_street|service|unclassified|cycleway|tertiary|secondary|primary)$\"]"
            f"({south:.6f},{west:.6f},{north:.6f},{east:.6f});"
        ),
    ]
    for selector in queries:
        query = f"[out:json][timeout:25];({selector});out tags geom;"
        try:
            data = post_overpass(query)
        except Exception:
            continue
        for element in data.get("elements", []):
            if element.get("type") != "way" or not element.get("geometry"):
                continue
            ways[element["id"]] = element
    return list(ways.values())


def post_overpass(query):
    encoded = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_error = None
    for url in OVERPASS_URLS:
        request = urllib.request.Request(
            url,
            data=encoded,
            method="POST",
            headers={"User-Agent": "training-coach-surface-sync/1.0"},
        )
        for attempt in range(SURFACE_OVERPASS_RETRIES + 1):
            try:
                with urllib.request.urlopen(request, timeout=OVERPASS_TIMEOUT_SECS) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as e:
                last_error = e
                if attempt < SURFACE_OVERPASS_RETRIES:
                    time.sleep(SURFACE_OVERPASS_RETRY_WAIT_SECS * (attempt + 1))
    raise last_error


def bbox(points, margin):
    lats = [point[1] for point in points]
    lons = [point[2] for point in points]
    return min(lats) - margin, min(lons) - margin, max(lats) + margin, max(lons) + margin


def nearest_way(start, end, ways):
    if not ways:
        return None, None
    mid_lat = (start[1] + end[1]) / 2
    mid_lon = (start[2] + end[2]) / 2
    best_way = None
    best_distance = None
    for way in ways:
        geometry = way.get("geometry", [])
        for a, b in zip(geometry, geometry[1:]):
            distance = point_to_segment_m(mid_lat, mid_lon, a["lat"], a["lon"], b["lat"], b["lon"])
            if best_distance is None or distance < best_distance:
                best_way = way
                best_distance = distance
    if best_distance is None or best_distance > SURFACE_MATCH_RADIUS_M:
        return None, best_distance
    return best_way, best_distance


def classify_surface(tags, match_distance_m):
    if not tags or match_distance_m is None or match_distance_m > SURFACE_MATCH_RADIUS_M:
        return "unknown", "unknown"

    surface = normalized(tags.get("surface"))
    if surface in ASPHALT_VALUES:
        return "asphalt", "tagged"
    if surface in CONCRETE_VALUES:
        return "concrete", "tagged"
    if surface in PAVED_VALUES:
        return "paved_other", "tagged"
    if surface in GRAVEL_VALUES:
        return "gravel", "tagged"
    if surface in TRAIL_VALUES:
        return "trail", "tagged"

    tracktype = normalized(tags.get("tracktype"))
    if tracktype in {"grade1", "grade2"}:
        return "gravel", "inferred"
    if tracktype in {"grade3", "grade4", "grade5"}:
        return "trail", "inferred"

    highway = normalized(tags.get("highway"))
    if highway in PAVED_HIGHWAYS:
        return "asphalt", "inferred"
    if highway == "cycleway":
        return "paved_other", "inferred"
    if highway == "track":
        return "gravel", "inferred"
    if highway in TRAIL_HIGHWAYS:
        return "trail", "inferred"
    return "unknown", "unknown"


def normalized(value):
    return str(value).strip().lower() if value not in (None, "") else None


def summarize_totals(totals, source):
    rounded = {group: round(totals.get(group, 0.0), 2) for group in SURFACE_GROUPS}
    total_km = round(sum(rounded.values()), 2)
    return {
        "surface_source": source,
        "surface_total_km": total_km,
        "distance_asphalt_km": rounded["asphalt"],
        "distance_concrete_km": rounded["concrete"],
        "distance_paved_other_km": rounded["paved_other"],
        "distance_gravel_km": rounded["gravel"],
        "distance_trail_km": rounded["trail"],
        "distance_unknown_km": rounded["unknown"],
        "hard_surface_km": round(sum(rounded[group] for group in HARD_SURFACES), 2),
        "soft_surface_km": round(sum(rounded[group] for group in SOFT_SURFACES), 2),
        "surface_breakdown_json": json.dumps(rounded, sort_keys=True),
    }


def empty_summary(source):
    return summarize_totals({group: 0.0 for group in SURFACE_GROUPS}, source)


def haversine_km(lat1, lon1, lat2, lon2):
    radius_km = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def point_to_segment_m(lat, lon, lat1, lon1, lat2, lon2):
    x, y = project_m(lat, lon, lat)
    x1, y1 = project_m(lat1, lon1, lat)
    x2, y2 = project_m(lat2, lon2, lat)
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    nearest_x = x1 + t * dx
    nearest_y = y1 + t * dy
    return math.hypot(x - nearest_x, y - nearest_y)


def project_m(lat, lon, ref_lat):
    meters_per_degree_lat = 111_320
    meters_per_degree_lon = 111_320 * math.cos(math.radians(ref_lat))
    return lon * meters_per_degree_lon, lat * meters_per_degree_lat
