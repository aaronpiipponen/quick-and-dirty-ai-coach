# AI Personal Coach Example

**Role:** This repository demonstrates an opencode-based endurance and hybrid fitness coach that works from Garmin-derived SQLite data, stable user context, current coach notes, and a user-facing training plan.

**Workflow:**
1. **Initialize Context:** Read `coach-example/coach_instructions.md` for coaching policy and operating rules.
2. **Review User Profile:** Read `user-example/user_profile.md` for stable user context.
3. **Review Coach Notes:** Read `coach-example/coach_notes.md` for current phase status, trends, and watchpoints.
4. **Review Training Plan:** Read `user-example/training_plan.md` for user-facing schedule, pacing, nutrition, and progression rules.
5. **Query Data:** Use `sql/schema.sql`, `sql/database_notes.md`, and `sql/example_queries.sql` as the database reference. A real `sql/user_data.db` is intentionally not included.
6. **Update Context:** Keep profile and coach notes current in a private working copy. Do not store real user health, GPS, credentials, or database files in this public example.

**File Roles:**
- `coach-example/`: Public-safe example coach instructions and current notes.
- `user-example/`: Public-safe example user profile and training plan.
- `scripts/`: Garmin sync and inspection utilities.
- `sql/schema.sql`: SQLite schema without private data.
- `sql/database_notes.md`: Database usage and coaching interpretation notes.
- `sql/example_queries.sql`: Example SQL queries.
- `.env.example`: Environment variable template only. Never commit `.env`.
