# User Data Notes

Local database: `src/db/user_data.db`

Use `src/db/schema.py` as the structural source of truth for table and column definitions. Keep this file for database usage notes and coaching interpretation only, so schema updates do not need to be duplicated in Markdown.

## Querying

Run ad hoc queries from the project root with:

```bash
sqlite3 -column -header src/db/user_data.db "YOUR QUERY"
```

For multi-line or complex queries, write them to a temporary `.sql` file and run:

```bash
sqlite3 -column -header src/db/user_data.db < src/db/query.sql
```

SQLite supports `json_array_length()` and `json_each()` for stream columns, but for most coaching analysis it is easier to fetch the raw JSON text and interpret the list directly.

## Table Roles

- `daily_summary`: one row per calendar day; primary source for health, sleep, recovery, HRV, training readiness, training load, and daily activity trends.
- `workouts`: one row per Garmin activity; primary source for endurance volume, intensity distribution, pacing, workout notes, and session-level load.
- `workout_routes`: GPS route summary and sampled coordinates for tracked cardio workouts.
- `workout_weather`: Open-Meteo weather matched to GPS workout route center and time window.
- `strength_sets`: active exercise sets from strength sessions; REST periods are excluded.
- `coach_decisions`: durable coach decision log for load changes, go/no-go calls, watchpoint follow-ups, and resolved/superseded coaching decisions.

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
- Use `coach_decisions` to preserve why a coaching call was made, what data or session it was linked to, and when it should be reviewed. Keep routine rolling context in `coach/coach_notes.md`; use this table for decisions worth querying later.

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

## Coach Decision Log

`coach_decisions` is for durable decisions, not every observation. Good entries include load reductions, event readiness calls, injury-risk constraints, plan changes, or follow-up tests.

Before making or changing a coaching decision, query this table for active/recent rows in the same topic, date range, workout, or context. If the same question has already been answered under materially similar conditions, reuse or update the existing row instead of inserting a duplicate. Same-day repeated questions with the same effective coaching call should usually be one combined entry. Mark rows `resolved` or `superseded` when they no longer apply.

- `date`: date the decision applies to or was made.
- `topic`: short category such as `load`, `injury`, `event`, `nutrition`, `gear`, or `strength`.
- `decision`: concise coaching call.
- `reason`: brief rationale, including the decisive context.
- `linked_activity_id`: optional Garmin activity link when the decision came from a workout.
- `linked_date`: optional daily-summary or plan date link when no single activity applies.
- `status`: `active`, `resolved`, or `superseded`.
- `next_review_date`: optional date to revisit the decision.
