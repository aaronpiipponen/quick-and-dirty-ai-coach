import argparse
import datetime
import json
import os
import shutil

from dotenv import load_dotenv
from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectConnectionError


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def login_with_cache(api, tokenstore):
    os.makedirs(tokenstore, exist_ok=True)
    try:
        api.login(tokenstore=tokenstore)
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as e:
        print(f"Cached Garmin login failed ({e}); clearing token cache and re-authenticating.")
        shutil.rmtree(tokenstore, ignore_errors=True)
        os.makedirs(tokenstore, exist_ok=True)
        api.login(tokenstore=tokenstore)


def print_payload(name, payload):
    print(f"\n{'=' * 72}")
    print(name)
    print(f"{'=' * 72}")
    if payload is None:
        print("None")
        return
    print(json.dumps(payload, indent=2, sort_keys=True)[:8000])


def main():
    parser = argparse.ArgumentParser(
        description="Inspect Garmin payload naming for daily recovery/training stats."
    )
    parser.add_argument(
        "-date",
        type=datetime.date.fromisoformat,
        default=datetime.date.today(),
        metavar="YYYY-MM-DD",
        help="Date to inspect. Defaults to today.",
    )
    args = parser.parse_args()
    date_str = args.date.isoformat()

    load_dotenv()
    tokenstore = os.getenv("GARMIN_TOKENSTORE", os.path.join(PROJECT_ROOT, ".garminconnect"))
    if not os.path.isabs(tokenstore):
        tokenstore = os.path.join(PROJECT_ROOT, tokenstore)
    api = Garmin(
        os.environ["GARMIN_USERNAME"],
        os.environ["GARMIN_PASSWORD"],
        retry_attempts=int(os.getenv("GARMIN_RETRY_ATTEMPTS", "5")),
        retry_min_wait=float(os.getenv("GARMIN_RETRY_MIN_WAIT", "2")),
        retry_max_wait=float(os.getenv("GARMIN_RETRY_MAX_WAIT", "30")),
    )
    login_with_cache(api, tokenstore)

    endpoints = [
        ("HRV", api.get_hrv_data),
        ("Respiration", api.get_respiration_data),
        ("Sleep", api.get_sleep_data),
        ("Training Status", api.get_training_status),
        ("Morning Training Readiness", api.get_morning_training_readiness),
    ]

    print(f"Inspecting Garmin daily fields for {date_str}")
    for name, getter in endpoints:
        try:
            print_payload(name, getter(date_str))
        except Exception as e:
            print_payload(name, {"error": str(e)})


if __name__ == "__main__":
    main()
