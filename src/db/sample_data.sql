INSERT OR REPLACE INTO daily_summary (
    date, weight_kg, resting_hr, max_hr, total_steps, calories_active,
    intensity_minutes, sleep_duration_mins, sleep_score, sleep_deep_mins,
    sleep_light_mins, sleep_rem_mins, sleep_awake_mins, stress_stream,
    body_battery_stream, respiration_stream, hrv_last_night_avg,
    hrv_weekly_avg, hrv_status, training_status_feedback,
    training_load_balance_feedback, training_load, training_readiness
) VALUES
('2026-06-04', 72.4, 52, 190, 8200, 620, 35, 455, 82, 78, 260, 95, 22,
 '[22,24,31,38,35,28,25]', '[78,74,66,58,51,46,42]', '[13.8,14.0,14.2,14.1,13.9,14.0,13.8]',
 62.0, 61.0, 'balanced', 'Productive', 'Load balanced', 315.0, 76),
('2026-06-05', 72.3, 53, 190, 10400, 780, 48, 430, 76, 70, 248, 82, 30,
 '[24,27,35,42,39,31,28]', '[75,70,62,55,49,43,39]', '[13.9,14.1,14.3,14.2,14.0,14.2,13.9]',
 60.0, 61.0, 'balanced', 'Productive', 'Load balanced', 332.0, 72),
('2026-06-06', 72.5, 54, 190, 6400, 410, 22, 390, 64, 54, 230, 72, 34,
 '[28,33,45,52,48,37,32]', '[68,61,52,45,38,32,28]', '[14.2,14.4,14.6,14.5,14.3,14.4,14.1]',
 56.0, 60.0, 'unbalanced', 'Recovery', 'Load slightly high', 348.0, 58),
('2026-06-07', 72.4, 55, 190, 11800, 860, 55, 445, 79, 74, 252, 92, 27,
 '[23,26,34,39,36,29,25]', '[76,71,64,57,50,45,40]', '[13.8,14.0,14.1,14.0,13.9,14.0,13.8]',
 59.0, 60.0, 'balanced', 'Maintaining', 'Load balanced', 360.0, 70),
('2026-06-08', 72.2, 53, 190, 7100, 450, 25, 470, 86, 82, 270, 100, 18,
 '[20,22,28,33,30,24,21]', '[82,78,72,65,59,53,49]', '[13.7,13.9,14.0,13.8,13.7,13.9,13.7]',
 63.0, 61.0, 'balanced', 'Maintaining', 'Load balanced', 342.0, 81),
('2026-06-09', 72.1, 56, 190, 16800, 1180, 92, 410, 68, 62, 238, 80, 30,
 '[25,31,44,55,50,39,34]', '[73,66,55,44,35,27,22]', '[14.0,14.3,14.7,14.8,14.5,14.4,14.1]',
 55.0, 60.0, 'unbalanced', 'Strained', 'Load high', 392.0, 49),
('2026-06-10', 72.2, 55, 190, 5200, 310, 12, 485, 88, 86, 278, 104, 17,
 '[18,20,24,29,27,22,19]', '[80,77,72,68,63,58,54]', '[13.6,13.8,13.9,13.8,13.7,13.8,13.6]',
 60.0, 60.0, 'balanced', 'Recovery', 'Load balanced', 370.0, 74);

INSERT OR REPLACE INTO workouts (
    activity_id, date, sport, name, notes, total_duration_mins, distance_km,
    avg_pace, avg_hr, max_hr, avg_cadence, elevation_gain, elevation_loss,
    steps, calories, zone1_mins, zone2_mins, zone3_mins, zone4_mins,
    zone5_mins, hr_stream, pace_stream, elevation_stream, cadence_stream,
    moving_duration_mins, elapsed_duration_mins, avg_moving_pace,
    downsampling_rate_secs
) VALUES
(9001, '2026-06-05', 'running', 'Easy run/walk',
 'Comfortable aerobic effort. Calf felt normal during run.',
 46.0, 6.4, '7:11', 136, 154, 162, 45, 44, 7600, 430,
 18, 25, 3, 0, 0,
 '[118,126,134,138,141,139,136,132]', '[7:45,7:22,7:08,7:02,7:05,7:12,7:18,7:30]',
 '[102,108,115,120,118,112,106,101]', '[158,160,162,164,163,162,160,158]',
 44.5, 46.0, '6:57', 300),
(9002, '2026-06-07', 'strength_training', 'Strength maintenance',
 'Kept lower body controlled. No calf pain, mild tightness after split squats.',
 38.0, NULL, NULL, 104, 132, NULL, NULL, NULL, NULL, 180,
 30, 8, 0, 0, 0,
 '[88,96,104,112,118,108]', NULL, NULL, NULL,
 38.0, 42.0, NULL, 300),
(9003, '2026-06-09', 'trail_running', 'Long easy trail session',
 'Felt good aerobically but calf tightened on climbs after 70 minutes. Kept effort easy and walked steeper hills.',
 88.0, 10.8, '8:09', 143, 166, 158, 215, 214, 13400, 820,
 22, 54, 10, 2, 0,
 '[122,132,139,143,146,149,152,148,142]', '[8:40,8:18,8:05,7:58,8:02,8:25,8:55,8:35,8:12]',
 '[110,128,152,185,210,238,260,220,145]', '[154,156,158,160,159,157,154,156,158]',
 84.0, 88.0, '7:47', 300);

INSERT OR REPLACE INTO strength_sets (
    activity_id, set_order, exercise_name, category, reps, weight_kg, duration_secs
) VALUES
(9002, 1, 'Goblet squat', 'legs', 10, 22.5, NULL),
(9002, 2, 'Goblet squat', 'legs', 10, 22.5, NULL),
(9002, 3, 'Romanian deadlift', 'posterior_chain', 8, 40.0, NULL),
(9002, 4, 'Romanian deadlift', 'posterior_chain', 8, 40.0, NULL),
(9002, 5, 'Split squat', 'legs', 8, 12.0, NULL),
(9002, 6, 'Side plank', 'core', NULL, NULL, 40);

INSERT OR REPLACE INTO workout_routes (
    activity_id, start_time_utc, end_time_utc, point_count, start_lat,
    start_lon, end_lat, end_lon, min_lat, max_lat, min_lon, max_lon,
    center_lat, center_lon, sampled_points_json, surface_source, surface_total_km,
    distance_asphalt_km, distance_concrete_km, distance_paved_other_km,
    distance_gravel_km, distance_trail_km, distance_unknown_km,
    hard_surface_km, soft_surface_km, surface_breakdown_json
) VALUES
(9003, '2026-06-09T08:00:00+00:00', '2026-06-09T09:28:00+00:00', 420,
  60.1700, 24.9400, 60.1760, 24.9520, 60.1680, 60.1810, 24.9360, 24.9580,
  60.1745, 24.9470,
  '[{"t":"2026-06-09T08:00:00+00:00","lat":60.170000,"lon":24.940000},{"t":"2026-06-09T08:30:00+00:00","lat":60.174000,"lon":24.946000},{"t":"2026-06-09T09:00:00+00:00","lat":60.181000,"lon":24.958000},{"t":"2026-06-09T09:28:00+00:00","lat":60.176000,"lon":24.952000}]',
  'sample-osm', 10.8, 1.6, 0.0, 0.4, 5.8, 3.0, 0.0, 2.0, 8.8,
  '{"asphalt": 1.6, "concrete": 0.0, "gravel": 5.8, "paved_other": 0.4, "trail": 3.0, "unknown": 0.0}');

INSERT OR REPLACE INTO workout_surface_segments (
    activity_id, sample_order, start_time_utc, end_time_utc, distance_km,
    surface, surface_source, surface_confidence, raw_surface, raw_highway,
    raw_tracktype, match_distance_m, tags_json
) VALUES
(9003, 1, '2026-06-09T08:00:00+00:00', '2026-06-09T08:30:00+00:00', 3.2,
 'gravel', 'sample-osm', 'tagged', 'fine_gravel', 'track', 'grade2', 8.0,
 '{"highway":"track","surface":"fine_gravel","tracktype":"grade2"}'),
(9003, 2, '2026-06-09T08:30:00+00:00', '2026-06-09T09:00:00+00:00', 4.6,
 'trail', 'sample-osm', 'inferred', NULL, 'path', NULL, 12.0,
 '{"highway":"path"}'),
(9003, 3, '2026-06-09T09:00:00+00:00', '2026-06-09T09:28:00+00:00', 3.0,
 'asphalt', 'sample-osm', 'tagged', 'asphalt', 'service', NULL, 5.0,
 '{"highway":"service","surface":"asphalt"}');

INSERT OR REPLACE INTO workout_weather (
    activity_id, weather_source, latitude, longitude, start_time_utc,
    end_time_utc, avg_temp_c, min_temp_c, max_temp_c, avg_humidity_pct,
    precipitation_mm, rain_mm, snowfall_mm, avg_wind_kmh,
    max_wind_gust_kmh, weather_codes_json, raw_hourly_json
) VALUES
(9003, 'open-meteo', 60.1745, 24.9470, '2026-06-09T08:00:00+00:00',
 '2026-06-09T09:28:00+00:00', 19.8, 18.9, 20.7, 64.0, 0.0, 0.0, 0.0,
 11.5, 22.0, '[2,2]', '{"temperature_2m":[18.9,20.7],"relative_humidity_2m":[67,61],"wind_speed_10m":[10.8,12.2]}');

INSERT OR REPLACE INTO coach_decisions (
    decision_id, date, topic, decision, reason, linked_activity_id,
    linked_date, status, next_review_date
) VALUES
(1, '2026-06-09', 'calf_load',
 'Keep the next endurance session easy and avoid hills until calf stiffness returns to baseline.',
 'Long trail session produced calf tightness on climbs after 70 minutes while readiness was low.',
 9003, '2026-06-09', 'active', '2026-06-11');
