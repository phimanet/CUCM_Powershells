import csv
import datetime
import getpass
import json
import os

import requests
import urllib3
from requests.auth import HTTPBasicAuth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OUTPUT_DIR = "output_logs"
UNITY_LAB_SERVER = "lascutypl01.ahs.int"


def make_unity_url(server, path):
    server = server.strip()
    if server.startswith("http://") or server.startswith("https://"):
        base = server.rstrip("/")
    else:
        base = f"https://{server}"
    return f"{base}{path}"


def unity_headers():
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def parse_error_text(response):
    text = (response.text or "").strip()
    if not text:
        return f"HTTP {response.status_code} with empty response body"
    return f"HTTP {response.status_code}: {text[:1200]}"


def get_user_by_alias(session, unity_server, alias):
    query = f"(Alias is {alias})"
    url = make_unity_url(unity_server, "/vmrest/users")
    response = session.get(url, headers=unity_headers(), params={"query": query}, timeout=120)

    if response.status_code != 200:
        raise RuntimeError(f"User lookup failed: {parse_error_text(response)}")

    if not response.text:
        return None

    try:
        data = response.json()
    except ValueError:
        return None

    users = data.get("User")
    if isinstance(users, dict):
        users = [users]
    if not isinstance(users, list):
        return None

    for user in users:
        if str(user.get("Alias", "")).lower() == alias.lower():
            return user

    return None


def get_user_by_object_id(session, unity_server, object_id):
    url = make_unity_url(unity_server, f"/vmrest/users/{object_id}")
    response = session.get(url, headers=unity_headers(), timeout=120)

    if response.status_code != 200:
        raise RuntimeError(f"User detail lookup failed: {parse_error_text(response)}")

    if not response.text:
        return None

    try:
        data = response.json()
    except ValueError:
        return None

    if isinstance(data, dict):
        return data

    return None


def flatten_for_diff(value, prefix=""):
    flattened = {}

    if isinstance(value, dict):
        for key, item in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(flatten_for_diff(item, child_prefix))
        return flattened

    if isinstance(value, list):
        for idx, item in enumerate(value):
            child_prefix = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            flattened.update(flatten_for_diff(item, child_prefix))
        if not value and prefix:
            flattened[prefix] = "[]"
        return flattened

    if prefix:
        if value is None:
            flattened[prefix] = "<null>"
        else:
            flattened[prefix] = str(value)
    return flattened


def write_user_export(alias, phase, timestamp, user_payload):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    export_file = os.path.join(OUTPUT_DIR, f"unity_user_export_{alias}_{phase}_{timestamp}.json")
    with open(export_file, "w", encoding="utf-8") as export_handle:
        json.dump(user_payload or {}, export_handle, indent=2, sort_keys=True)
    return export_file


def write_user_diff_report(alias, timestamp, before_payload, after_payload):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    diff_file = os.path.join(OUTPUT_DIR, f"unity_user_diff_{alias}_{timestamp}.csv")

    before_flat = flatten_for_diff(before_payload or {})
    after_flat = flatten_for_diff(after_payload or {})
    all_keys = sorted(set(before_flat.keys()) | set(after_flat.keys()))

    with open(diff_file, "w", newline="", encoding="utf-8") as diff_handle:
        writer = csv.writer(diff_handle)
        writer.writerow(["Field", "Before", "After"])
        for key in all_keys:
            before_value = before_flat.get(key, "<missing>")
            after_value = after_flat.get(key, "<missing>")
            if before_value != after_value:
                writer.writerow([key, before_value, after_value])

    return diff_file


def main():
    print("===============================================================")
    print(" Unity User Export + Compare (Before/After LDAP Integration)")
    print("===============================================================\n")

    unity_server = input(
        f"Enter Cisco Unity Connection server (or press Enter for LAB: {UNITY_LAB_SERVER}), or 0 to return: "
    ).strip()
    if unity_server == "0":
        return
    if not unity_server:
        unity_server = UNITY_LAB_SERVER

    admin_user = input("Enter Unity admin username: ").strip()
    if not admin_user:
        print("No admin username provided. Exiting.")
        return

    admin_pass = getpass.getpass("Enter Unity admin password: ")
    target_alias = input("Enter target alias (press Enter for Vijiha.Barner): ").strip() or "Vijiha.Barner"

    session = requests.Session()
    session.verify = False
    session.auth = HTTPBasicAuth(admin_user, admin_pass)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    existing_user = get_user_by_alias(session, unity_server, target_alias)
    if not existing_user:
        raise RuntimeError(f"User '{target_alias}' not found in Unity.")

    object_id = existing_user.get("ObjectId")
    if not object_id:
        raise RuntimeError(f"User '{target_alias}' found, but ObjectId was not returned.")

    before_payload = get_user_by_object_id(session, unity_server, object_id)
    before_file = write_user_export(target_alias, "before_ldap", timestamp, before_payload)
    print(f"Before export saved: {before_file}")

    print("\nNow do this in Unity UI:")
    print("1. Open the user.")
    print("2. Check Integrate with LDAP Directory.")
    print("3. Save.")
    proceed = input("\nPress Enter when done, or type 'cancel' to stop: ").strip().lower()
    if proceed == "cancel":
        print("Canceled after before-export.")
        return

    after_payload = get_user_by_object_id(session, unity_server, object_id)
    after_file = write_user_export(target_alias, "after_ldap", timestamp, after_payload)
    print(f"After export saved: {after_file}")

    diff_file = write_user_diff_report(target_alias, timestamp, before_payload, after_payload)
    print(f"Diff report saved: {diff_file}")
    print("\nDone.")


if __name__ == "__main__":
    main()
