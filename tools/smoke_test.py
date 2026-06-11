#!/usr/bin/env python3
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run(args, check=True):
    result = subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if check and result.returncode != 0:
        raise SystemExit(f"Command failed: python {' '.join(args)}\n{result.stdout}")
    return result


def create_import_source(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE activities (id INTEGER PRIMARY KEY, start_date TEXT, total_time REAL)")
        conn.execute("INSERT INTO activities VALUES (1, '2026-06-05', 42.0)")
        conn.commit()
    finally:
        conn.close()


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        sample_db = tmp_path / "sample.db"
        source_db = tmp_path / "source.db"
        good_map = tmp_path / "good_mapping.json"
        bad_map = tmp_path / "bad_mapping.json"

        run(["src/db/init.py", "--db", str(sample_db), "--sample"])
        context = run(["tools/coach_context.py", "--db", str(sample_db)])
        if "Anchor date: 2026-06-10" not in context.stdout:
            raise SystemExit("Smoke test failed: sample coach context anchor date was not 2026-06-10")
        if "calf_load" not in context.stdout:
            raise SystemExit("Smoke test failed: sample coach context did not include calf decision")

        run(["src/sync.py", "--help"])
        run(["src/sync.py", "sqlite_import", "--help"])

        create_import_source(source_db)
        write_json(
            good_map,
            {
                "tables": {
                    "workouts": {
                        "source": "activities",
                        "columns": {
                            "activity_id": "id",
                            "date": "start_date",
                            "elapsed_duration_mins": "total_time",
                        },
                    }
                }
            },
        )
        good = run([
            "src/sync.py", "sqlite_import", "--input", str(source_db),
            "--map", str(good_map), "--dry-run", "--table", "workouts",
        ])
        if "Planned rows: workouts=1" not in good.stdout:
            raise SystemExit("Smoke test failed: valid sqlite import dry-run did not plan one workout")

        write_json(
            bad_map,
            {"tables": {"workouts": {"source": "activities", "columns": {"activity_id": "missing_id"}}}},
        )
        bad = run([
            "src/sync.py", "sqlite_import", "--input", str(source_db),
            "--map", str(bad_map), "--dry-run", "--table", "workouts",
        ], check=False)
        expected = "Error: Mapping for workouts.activity_id references missing source column"
        if bad.returncode == 0 or expected not in bad.stdout or "Traceback" in bad.stdout:
            raise SystemExit("Smoke test failed: invalid mapping did not fail cleanly")

    print("Smoke test passed.")


if __name__ == "__main__":
    main()
