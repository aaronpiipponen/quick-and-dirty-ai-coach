# SQLite Import Mapping Example

`sqlite_import` can copy data from another SQLite database into this project's normalized coaching schema. If the source database uses different table or column names, pass a mapping file with `--map`.

The mapping file itself should be JSON. This Markdown file is only an annotated example.

## Command

```bash
python src/sync.py sqlite_import --input old_training.db --map mapping.json --dry-run
```

Use `--dry-run` or `--plan` first. Remove it when the plan looks correct.

## Mapping Format

```json
{
  "tables": {
    "daily_summary": {
      "source": "daily_health",
      "columns": {
        "date": "day",
        "resting_hr": "resting_heart_rate",
        "sleep_duration_mins": "sleep_minutes",
        "hrv_last_night_avg": "overnight_hrv"
      }
    },
    "workouts": {
      "source": "activities",
      "columns": {
        "activity_id": "id",
        "date": "start_date",
        "sport": "activity_type",
        "name": "activity_name",
        "distance_km": "kilometers",
        "elapsed_duration_mins": "total_time",
        "avg_hr": "average_hr"
      }
    }
  }
}
```

## Rules

- Top-level keys under `tables` are target tables in this project.
- `source` is the table name in the input SQLite database. `old_name` also works.
- Each `columns` entry maps `target_column_name` to `source_column_name`.
- Columns with the same name in both databases do not need to be listed.
- `--auto-map` can infer simple table and column matches, including common aliases.
- `--strict` fails if a target table or column cannot be mapped, except `decision_id`.
- `--since` and `--until` only work for a table if its target date column is mapped.
- Values are copied as-is. The importer does not convert units, timestamps, or pace formats.
- Explicit mapping mistakes fail before writing, including unknown target tables, unknown target columns, missing source tables, and missing source columns.

Use this to inspect the source database before writing a mapping:

```bash
python src/sync.py sqlite_import --input old_training.db --print-schema
```
