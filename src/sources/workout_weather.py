import datetime
import json
import urllib.parse
import urllib.request


WEATHER_HOURLY_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "rain",
    "snowfall",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
]


def fetch_json(url, params):
    full_url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    with urllib.request.urlopen(full_url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_open_meteo_weather(lat, lon, start_iso, end_iso):
    start_dt = datetime.datetime.fromisoformat(start_iso)
    end_dt = datetime.datetime.fromisoformat(end_iso)
    params = {
        "latitude": round(lat, 5),
        "longitude": round(lon, 5),
        "start_date": start_dt.date().isoformat(),
        "end_date": end_dt.date().isoformat(),
        "hourly": WEATHER_HOURLY_FIELDS,
        "timezone": "UTC",
        "wind_speed_unit": "kmh",
    }
    endpoints = [
        ("open-meteo-archive", "https://archive-api.open-meteo.com/v1/archive"),
        ("open-meteo-forecast", "https://api.open-meteo.com/v1/forecast"),
    ]
    last_error = None
    for source, url in endpoints:
        try:
            data = fetch_json(url, params)
            if data.get("hourly"):
                return source, data
        except Exception as e:
            last_error = e
    raise RuntimeError(f"No Open-Meteo weather data returned: {last_error}")


def summarize_weather(source, data, start_iso, end_iso):
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    start_dt = datetime.datetime.fromisoformat(start_iso).replace(tzinfo=None)
    end_dt = datetime.datetime.fromisoformat(end_iso).replace(tzinfo=None)
    selected = []
    for i, value in enumerate(times):
        hour_dt = datetime.datetime.fromisoformat(value)
        if start_dt.replace(minute=0, second=0) <= hour_dt <= end_dt:
            selected.append(i)
    if not selected:
        selected = list(range(len(times)))

    def values(field):
        raw = hourly.get(field, [])
        return [raw[i] for i in selected if i < len(raw) and raw[i] is not None]

    def avg(field):
        vals = values(field)
        return round(sum(vals) / len(vals), 1) if vals else None

    def total(field):
        vals = values(field)
        return round(sum(vals), 1) if vals else None

    temps = values("temperature_2m")
    gusts = values("wind_gusts_10m")
    codes = sorted(set(values("weather_code")))
    raw_selected = {"time": [times[i] for i in selected]}
    for field in WEATHER_HOURLY_FIELDS:
        raw = hourly.get(field, [])
        raw_selected[field] = [raw[i] for i in selected if i < len(raw)]
    return {
        "weather_source": source,
        "avg_temp_c": avg("temperature_2m"),
        "min_temp_c": round(min(temps), 1) if temps else None,
        "max_temp_c": round(max(temps), 1) if temps else None,
        "avg_humidity_pct": avg("relative_humidity_2m"),
        "precipitation_mm": total("precipitation"),
        "rain_mm": total("rain"),
        "snowfall_mm": total("snowfall"),
        "avg_wind_kmh": avg("wind_speed_10m"),
        "max_wind_gust_kmh": round(max(gusts), 1) if gusts else None,
        "weather_codes_json": json.dumps(codes),
        "raw_hourly_json": json.dumps(raw_selected),
    }
