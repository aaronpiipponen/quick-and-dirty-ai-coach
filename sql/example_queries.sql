-- Example queries for sql/user_data.db.
-- Run from the project root with:
-- sqlite3 -column -header sql/user_data.db < sql/example_queries.sql

-- Recent health overview
SELECT date, weight_kg, resting_hr, sleep_duration_mins, sleep_score,
       hrv_last_night_avg, hrv_status, training_readiness,
       calories_active, intensity_minutes
FROM daily_summary
ORDER BY date DESC
LIMIT 14;

-- Last 4 weeks of workouts
SELECT date, sport, name,
       total_duration_mins, moving_duration_mins, elapsed_duration_mins,
       distance_km, avg_pace, avg_moving_pace, avg_hr,
       zone2_mins, zone4_mins + zone5_mins AS high_intensity_mins, calories
FROM workouts
WHERE date >= date('now', '-28 days')
ORDER BY date DESC;

-- Recent workouts with weather
SELECT w.date, w.sport, w.name, w.distance_km, w.avg_hr,
       ww.avg_temp_c, ww.avg_humidity_pct, ww.precipitation_mm,
       ww.avg_wind_kmh, ww.max_wind_gust_kmh
FROM workouts w
LEFT JOIN workout_weather ww ON ww.activity_id = w.activity_id
WHERE w.date >= date('now', '-28 days')
ORDER BY w.date DESC;

-- Route summary for a workout
SELECT w.date, w.name, r.point_count,
       r.start_lat, r.start_lon, r.end_lat, r.end_lon,
       r.center_lat, r.center_lon
FROM workouts w
JOIN workout_routes r ON r.activity_id = w.activity_id
WHERE w.activity_id = (
    SELECT activity_id
    FROM workout_routes
    ORDER BY activity_id DESC
    LIMIT 1
);

-- Weekly running volume
SELECT strftime('%Y-W%W', date) AS week,
       ROUND(SUM(distance_km), 1) AS total_km,
       COUNT(*) AS sessions,
       ROUND(AVG(avg_hr)) AS avg_hr
FROM workouts
WHERE sport IN ('running', 'trail_running', 'treadmill_running')
GROUP BY week
ORDER BY week DESC
LIMIT 12;

-- Strength progression for one exercise
SELECT w.date, s.set_order, s.reps, s.weight_kg, s.duration_secs
FROM workouts w
JOIN strength_sets s ON w.activity_id = s.activity_id
WHERE s.exercise_name = 'BENT_OVER_ROW_WITH_DUMBELL'
ORDER BY w.date, s.set_order;

-- Full strength session detail
SELECT w.date, w.name, w.notes, w.total_duration_mins, w.avg_hr,
       s.set_order, s.exercise_name, s.category, s.reps, s.weight_kg
FROM workouts w
JOIN strength_sets s ON w.activity_id = s.activity_id
WHERE w.sport = 'strength_training'
ORDER BY w.date DESC, s.set_order;

-- Sleep trend
SELECT date,
       ROUND(sleep_duration_mins / 60.0, 1) AS sleep_hrs,
       sleep_score, sleep_deep_mins, sleep_light_mins, sleep_rem_mins,
       resting_hr,
       hrv_last_night_avg, hrv_weekly_avg, hrv_status,
       body_battery_stream, respiration_stream
FROM daily_summary
WHERE sleep_duration_mins IS NOT NULL
ORDER BY date DESC
LIMIT 14;

-- Recovery and training-readiness trend
SELECT date, resting_hr, sleep_score,
       hrv_last_night_avg, hrv_weekly_avg, hrv_status,
       training_readiness, training_status_feedback,
       training_load, training_load_balance_feedback
FROM daily_summary
ORDER BY date DESC
LIMIT 14;

-- Zone 2 training load over time
SELECT strftime('%Y-W%W', date) AS week,
       SUM(zone2_mins) AS zone2_mins,
       SUM(zone3_mins + zone4_mins + zone5_mins) AS high_intensity_mins,
       COUNT(*) AS sessions
FROM workouts
WHERE sport IN ('running', 'walking', 'cycling', 'road_biking')
GROUP BY week
ORDER BY week DESC
LIMIT 12;

-- Stress, body battery, and respiration for a specific day
SELECT date, stress_stream, body_battery_stream, respiration_stream
FROM daily_summary
WHERE date = '2026-06-09';

-- Weight trend, non-null only
SELECT date, weight_kg
FROM daily_summary
WHERE weight_kg IS NOT NULL
ORDER BY date DESC
LIMIT 30;

-- Active coach decisions and follow-ups
SELECT decision_id, date, topic, decision, reason,
       linked_activity_id, linked_date, next_review_date
FROM coach_decisions
WHERE status = 'active'
ORDER BY COALESCE(next_review_date, date) ASC, decision_id ASC;

-- Check recent similar decisions before making a new coaching call
SELECT decision_id, date, topic, status, decision, reason,
       linked_activity_id, linked_date, next_review_date
FROM coach_decisions
WHERE date >= date('now', '-21 days')
  AND topic IN ('load', 'injury', 'event')
ORDER BY date DESC, decision_id DESC;

-- Recent coach decisions with linked workout context
SELECT d.decision_id, d.date, d.topic, d.status, d.decision, d.reason,
       w.date AS workout_date, w.sport, w.name, w.distance_km, w.avg_hr
FROM coach_decisions d
LEFT JOIN workouts w ON w.activity_id = d.linked_activity_id
ORDER BY d.date DESC, d.decision_id DESC
LIMIT 20;
