import csv
import datetime
import getpass
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

    return data if isinstance(data, dict) else None


def set_user_pin(session, unity_server, object_id, pin, must_change=True):
    url = make_unity_url(unity_server, f"/vmrest/users/{object_id}/credential/pin")
    payload = {
        "Credentials": pin,
        "CredMustChange": str(bool(must_change)).lower(),
    }

    response = session.put(url, headers=unity_headers(), json=payload, timeout=120)
    if response.status_code not in {200, 201, 204}:
        raise RuntimeError(f"Set PIN failed: {parse_error_text(response)}")


def confirm_yes_no(prompt, default_no=True):
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    value = input(prompt + suffix).strip().lower()
    if not value:
        return not default_no
    return value in {"y", "yes"}


def main():
    print("==================================================")
    print(" Cisco Unity Connection - Reset Voicemail PIN")
    print("==================================================\n")

    unity_server = input(
        f"Enter Cisco Unity Connection server (or press Enter for LAB: {UNITY_LAB_SERVER}), or 0 to return: "
    ).strip()
    if unity_server == "0":
        return
    if not unity_server:
        unity_server = UNITY_LAB_SERVER

    admin_user = input("Enter Unity admin username: ").strip()
    if not admin_user:
        print("No admin username provided. Returning to main menu.")
        return

    admin_pass = getpass.getpass("Enter Unity admin password: ")

    session = requests.Session()
    session.verify = False
    session.auth = HTTPBasicAuth(admin_user, admin_pass)

    while True:
        target_alias = input("Enter voicemail username to reset PIN for: ").strip()
        if not target_alias:
            print("No username entered. Exiting.")
            return

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        log_file = os.path.join(OUTPUT_DIR, f"reset_unity_voicemail_pin_{target_alias}_{timestamp}.csv")
        run_success = False

        with open(log_file, "w", newline="", encoding="utf-8") as logfile:
            writer = csv.writer(logfile)
            writer.writerow(["Step", "Status", "Details"])

            try:
                existing_user = get_user_by_alias(session, unity_server, target_alias)
                if not existing_user:
                    raise RuntimeError(f"Unity mailbox '{target_alias}' was not found.")

                object_id = str(existing_user.get("ObjectId") or "").strip()
                if not object_id:
                    raise RuntimeError(f"Unity mailbox '{target_alias}' was found, but ObjectId was missing.")

                user_detail = get_user_by_object_id(session, unity_server, object_id)
                if not user_detail:
                    raise RuntimeError(f"Could not retrieve full Unity mailbox details for '{target_alias}'.")

                mailbox_alias = str(user_detail.get("Alias") or target_alias).strip()
                extension = str(user_detail.get("DtmfAccessId") or "").strip()
                first_name = str(user_detail.get("FirstName") or "").strip()
                last_name = str(user_detail.get("LastName") or "").strip()

                writer.writerow([
                    "Lookup Mailbox",
                    "Success",
                    f"Alias={mailbox_alias}; Extension={extension}; FirstName={first_name}; LastName={last_name}",
                ])

                print("\nMailbox found:")
                print(f"  Username : {mailbox_alias}")
                print(f"  Extension: {extension}")
                print(f"  FirstName: {first_name}")
                print(f"  LastName : {last_name}")

                if not confirm_yes_no("Reset PIN for this mailbox?", default_no=True):
                    writer.writerow(["Confirmation", "Cancelled", "Operator cancelled after mailbox validation"])
                    print(f"Cancelled. Results logged to: {log_file}")
                else:
                    new_pin = getpass.getpass("Enter new voicemail PIN: ").strip()
                    if not new_pin:
                        raise RuntimeError("No PIN entered.")

                    set_user_pin(session, unity_server, object_id, new_pin, must_change=True)
                    writer.writerow([
                        "Reset PIN",
                        "Success",
                        f"Reset voicemail PIN for {mailbox_alias}; must-change at next login enabled",
                    ])
                    print("PIN reset successfully.")
                    print(f"Results logged to: {log_file}")
                    run_success = True

            except Exception as exc:
                writer.writerow(["Script", "Error", str(exc)])
                print(f"\nScript failed: {exc}")
                print(f"Results logged to: {log_file}")

        prompt_text = "Reset another voicemail PIN using the same username/password?"
        if not run_success:
            prompt_text = "Try another voicemail PIN reset using the same username/password?"
        if not confirm_yes_no(prompt_text, default_no=True):
            return


if __name__ == "__main__":
    main()