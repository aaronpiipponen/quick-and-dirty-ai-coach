# User Data Notes

Local database: `sql/user_data.db`

Use `sql/schema.sql` as the structural source of truth for table and column definitions. Keep this file for database usage notes and coaching interpretation only, so schema updates do not need to be duplicated in Markdown.

## Querying

Run ad hoc queries from the project root with:

```bash
sqlite3 -column -header sql/user_data.db "YOUR QUERY"
```

For multi-line or complex queries, write them to a temporary `.sql` file and run:

```bash
sqlite3 -column -header sql/user_data.db < sql/query.sql
```

SQLite supports `json_array_length()` and `json_each()` for stream columns, but for most coaching analysis it is easier to fetch the raw JSON text and interpret the list directly.

## Table Roles

- `daily_summary`: one row per calendar day; primary source for health, sleep, recovery, HRV, training readiness, training load, and daily activity trends.
- `workouts`: one row per Garmin activity; primary source for endurance volume, intensity distribution, pacing, workout notes, and session-level load.
- `workout_routes`: GPS route summary and sampled coordinates for tracked cardio workouts.
- `workout_weather`: Open-Meteo weather matched to GPS workout route center and time window.
- `strength_sets`: active exercise sets from strength sessions; REST periods are excluded.

## Stream Interpretation

- Day-level streams in `daily_summary` are ordered hourly averages from Garmin timestamps.
- Workout streams in `workouts` use the interval in `workouts.downsampling_rate_secs`; default comes from `DOWNSAMPLE_INTERVAL_SECS` in `.env`.
- `stress_stream` uses Garmin's 0-100 stress scale. Values above ~50 are elevated.
- `body_battery_stream` uses Garmin's 0-100 body battery scale. Values below ~25 suggest fatigue.
- `respiration_stream` is breaths/min. Unusually elevated sleeping respiration can indicate heat stress, illness, alcohol, or poor recovery.
- `hr_stream` is heart rate in bpm.
- `pace_stream` values are pace strings in `M:SS` per km. Faster means a lower value.
- `cadence_stream` stores full cadence. Running cadence values should be comparable to `avg_cadence`.

## Coaching Interpretation

- Read workout `notes` carefully; subjective notes may explain session changes, pain, stops, or perceived effort better than the numbers alone.
- Zone 2 endurance work is `zone2_mins`. Zone 4-5 is high-intensity leakage or deliberate hard work.
- Use `workout_weather` when interpreting unusually high HR, pace drift, heavy perceived effort, dehydration risk, or poor recovery after outdoor sessions.
- Use `strength_sets.weight_kg`, `reps`, and `set_order` to assess strength maintenance or progression.
- User currently sleeps roughly 01:00-02:00 to 11:00-12:00. Morning body battery readings should usually be taken from entries 10-12 rather than entry 1.

## Units

- Weight: kg
- Distance: km
- Pace: `M:SS` per km
- Elevation: metres
- Heart rate: bpm
- Cadence: steps/min for running, revolutions/min for cycling
- Calories: active kcal only
- Sleep duration: minutes
- Set weight: kg
- Set duration: seconds
- Stress and body battery: 0-100 scale
