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
DEFAULT_VM_PIN = "56219"
UNITY_USER_TEMPLATE_ALIAS = "T3-CST"
EMAIL_DOMAIN = "@amnhealthcare.com"
LDAP_INTEGRATION_ENABLED = True


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


def extract_object_id_from_location(response):
    location = response.headers.get("Location", "").strip()
    if not location:
        return None
    clean = location.rstrip("/")
    if "/vmrest/users/" not in clean:
        return None
    return clean.split("/")[-1] or None


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


def user_is_ldap_integrated(user, expected_ldap_user_id=None):
    if not isinstance(user, dict):
        return False

    integrated_flag = user.get("IsLdapIntegrated")
    if isinstance(integrated_flag, bool) and integrated_flag:
        return True
    if isinstance(integrated_flag, str) and integrated_flag.strip().lower() in {"true", "1", "yes"}:
        return True

    # On this Unity build, LDAP linkage is reflected by directory-backed fields.
    ldap_type = str(user.get("LdapType") or "").strip()
    ldap_pkid = str(user.get("LdapCcmPkid") or "").strip()
    ldap_user_id = str(user.get("LdapCcmUserId") or "").strip()

    if ldap_type == "3" and ldap_pkid:
        if not expected_ldap_user_id:
            return True
        return ldap_user_id.lower() == str(expected_ldap_user_id).strip().lower()

    return False


def get_import_user_by_alias(session, unity_server, alias):
    """Lookup an importable LDAP user via CUPI Import Users endpoint."""
    endpoint = make_unity_url(unity_server, "/vmrest/import/users/ldap")
    queries = [
        f"(alias is {alias})",
        f"(alias is {alias.lower()})",
        f"(alias startswith {alias.split('.')[0]})",
    ]

    for query in queries:
        response = session.get(endpoint, headers=unity_headers(), params={"query": query}, timeout=120)

        if response.status_code == 404:
            raise RuntimeError("LDAP import endpoint /vmrest/import/users/ldap is not available.")
        if response.status_code in {401, 403}:
            raise RuntimeError("Access denied for /vmrest/import/users/ldap with current credentials.")
        if response.status_code != 200 or not response.text:
            continue

        try:
            data = response.json()
        except ValueError:
            continue

        import_users = data.get("ImportUser")
        if isinstance(import_users, dict):
            import_users = [import_users]
        if not isinstance(import_users, list) or not import_users:
            continue

        for import_user in import_users:
            import_alias = str(import_user.get("alias", "")).strip()
            if import_alias.lower() == alias.lower():
                return import_user

        # If query returned one candidate but alias key has different casing/format, use it.
        if len(import_users) == 1:
            return import_users[0]

    return None


def import_ldap_user_with_new_vm(session, unity_server, import_pkid, extension, template_alias):
    """Import LDAP user using CUPI import endpoint and return new Unity user ObjectId when available."""
    endpoint = make_unity_url(unity_server, "/vmrest/import/users/ldap")
    payload = {
        "dtmfAccessId": extension,
        "pkid": import_pkid,
    }

    response = session.post(
        endpoint,
        headers=unity_headers(),
        params={"templateAlias": template_alias},
        json=payload,
        timeout=120,
    )

    if response.status_code not in {200, 201, 204}:
        raise RuntimeError(f"LDAP import by pkid failed: {parse_error_text(response)}")

    object_id = extract_object_id_from_location(response)
    if object_id:
        return object_id

    body_text = (response.text or "").strip()
    if "/vmrest/users/" in body_text:
        return body_text.rstrip("/").split("/")[-1]

    return None


def update_existing_user_mailbox(session, unity_server, object_id, extension, email_address):
    url = make_unity_url(unity_server, f"/vmrest/users/{object_id}")
    payload = {
        "DtmfAccessId": extension,
        "EmailAddress": email_address,
        "IsLdapIntegrated": LDAP_INTEGRATION_ENABLED,
    }
    response = session.put(url, headers=unity_headers(), json=payload, timeout=120)
    if response.status_code not in {200, 204}:
        raise RuntimeError(f"Update mailbox extension failed: {parse_error_text(response)}")


def ensure_ldap_integration(session, unity_server, object_id, ldap_user_id):
    if not LDAP_INTEGRATION_ENABLED:
        return

    user = get_user_by_object_id(session, unity_server, object_id)
    if user_is_ldap_integrated(user, ldap_user_id):
        return

    # Some Unity builds only honor LDAP integration after explicitly setting LDAP identity fields.
    update_url = make_unity_url(unity_server, f"/vmrest/users/{object_id}")
    payload_attempts = [
        {"IsLdapIntegrated": True, "LdapCcmUserId": ldap_user_id},
        {"IsLdapIntegrated": "true", "LdapCcmUserId": ldap_user_id},
        {"LdapCcmUserId": ldap_user_id},
    ]

    last_error = None
    for payload in payload_attempts:
        response = session.put(update_url, headers=unity_headers(), json=payload, timeout=120)
        if response.status_code not in {200, 204}:
            last_error = parse_error_text(response)
            continue

        verified_user = get_user_by_object_id(session, unity_server, object_id)
        if user_is_ldap_integrated(verified_user, ldap_user_id):
            return

    if last_error:
        raise RuntimeError(f"Enable LDAP integration failed: {last_error}")

    verified_user = get_user_by_object_id(session, unity_server, object_id)
    if not user_is_ldap_integrated(verified_user, ldap_user_id):
        raise RuntimeError(
            "Unity created the mailbox but LDAP integration is still disabled after update. "
            "Verify LDAP user ID is correct and that LDAP import is enabled on this Unity cluster."
        )


def create_local_user_with_mailbox(
    session,
    unity_server,
    alias,
    first_name,
    last_name,
    display_name,
    extension,
    email_address,
):
    url = make_unity_url(unity_server, "/vmrest/users")
    payload = {
        "Alias": alias,
        "FirstName": first_name,
        "LastName": last_name,
        "DisplayName": display_name,
        "DtmfAccessId": extension,
        "EmailAddress": email_address,
        "IsLdapIntegrated": LDAP_INTEGRATION_ENABLED,
        "TemplateAlias": UNITY_USER_TEMPLATE_ALIAS,
    }

    response = session.post(url, headers=unity_headers(), json=payload, timeout=120)
    if response.status_code not in {200, 201}:
        # Some Unity versions require templateAlias in query string instead of JSON body.
        response_retry = session.post(
            url,
            headers=unity_headers(),
            params={"templateAlias": UNITY_USER_TEMPLATE_ALIAS},
            json={
                "Alias": alias,
                "FirstName": first_name,
                "LastName": last_name,
                "DisplayName": display_name,
                "DtmfAccessId": extension,
                "EmailAddress": email_address,
                "IsLdapIntegrated": LDAP_INTEGRATION_ENABLED,
            },
            timeout=120,
        )
        if response_retry.status_code not in {200, 201}:
            raise RuntimeError(f"Create user/mailbox failed: {parse_error_text(response_retry)}")
        response = response_retry

    if not response.text:
        return extract_object_id_from_location(response)

    try:
        data = response.json()
    except ValueError:
        # Some Unity responses are successful but return non-JSON/empty payloads.
        return extract_object_id_from_location(response)

    return data.get("ObjectId") or extract_object_id_from_location(response)


def set_user_pin(session, unity_server, object_id, pin, must_change=True):
    if not pin:
        return

    url = make_unity_url(unity_server, f"/vmrest/users/{object_id}/credential/pin")
    payload = {
        "Credentials": pin,
        "CredMustChange": str(bool(must_change)).lower(),
    }

    response = session.put(url, headers=unity_headers(), json=payload, timeout=120)
    if response.status_code not in {200, 201, 204}:
        raise RuntimeError(f"Set PIN failed: {parse_error_text(response)}")


def main():
    print("==================================================")
    print(" Cisco Unity Connection - Create Voicemail Box")
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
    target_alias = input("Username of the person who needs Cisco Jabber voicemail: ").strip()
    ldap_user_id = input("Enter LDAP User ID (press Enter to use username): ").strip() or target_alias
    first_name = input("Enter First Name: ").strip() or target_alias
    last_name = input("Enter Last Name: ").strip() or target_alias
    display_name = f"{last_name}, {first_name}".strip().strip(",")
    print(f"Using Display Name: {display_name}")
    default_email = f"{target_alias.lower()}{EMAIL_DOMAIN}"
    email_address = input(
        f"Enter email address (press Enter for default {default_email}): "
    ).strip() or default_email
    extension = input("Enter voicemail extension (mailbox number): ").strip()
    pin = getpass.getpass(f"Enter voicemail PIN (press Enter for default {DEFAULT_VM_PIN}): ")
    if not pin:
        pin = DEFAULT_VM_PIN

    if not target_alias or not extension:
        print("Username and extension are required. Returning to main menu.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(OUTPUT_DIR, f"create_unity_voicemail_{target_alias}_{timestamp}.csv")

    session = requests.Session()
    session.verify = False
    session.auth = HTTPBasicAuth(admin_user, admin_pass)

    with open(log_file, "w", newline="", encoding="utf-8") as logfile:
        writer = csv.writer(logfile)
        writer.writerow(["Step", "Status", "Details"])

        try:
            writer.writerow(["Connect", "Success", f"Unity server {unity_server}"])
            object_id = None
            ldap_api_available = True

            existing_user = get_user_by_alias(session, unity_server, target_alias)
            if existing_user:
                object_id = existing_user.get("ObjectId")
                if not object_id:
                    raise RuntimeError(
                        "Alias already exists but Unity did not return ObjectId for mailbox update."
                    )

                update_existing_user_mailbox(session, unity_server, object_id, extension, email_address)
                writer.writerow([
                    "Create User",
                    "Skipped",
                    f"Alias {target_alias} already exists; reused existing user and updated extension {extension}",
                ])
                print(
                    f"Alias '{target_alias}' already exists. Updated existing mailbox extension to {extension}."
                )

                if LDAP_INTEGRATION_ENABLED and not user_is_ldap_integrated(existing_user, ldap_user_id):
                    ldap_api_available = False
                    print(
                        "Existing user is not LDAP-integrated. "
                        "Manual LDAP checkbox step will be required."
                    )
            else:
                if LDAP_INTEGRATION_ENABLED:
                    try:
                        import_user = get_import_user_by_alias(session, unity_server, target_alias)
                        if not import_user and ldap_user_id.lower() != target_alias.lower():
                            import_user = get_import_user_by_alias(session, unity_server, ldap_user_id)

                        if not import_user:
                            raise RuntimeError(
                                f"No LDAP import candidate found for alias '{target_alias}' or LDAP ID '{ldap_user_id}'."
                            )

                        import_pkid = import_user.get("pkid")
                        if not import_pkid:
                            raise RuntimeError("ImportUser response did not include pkid.")

                        object_id = import_ldap_user_with_new_vm(
                            session,
                            unity_server,
                            import_pkid,
                            extension,
                            UNITY_USER_TEMPLATE_ALIAS,
                        )

                        if not object_id:
                            imported_user = get_user_by_alias(session, unity_server, target_alias)
                            if imported_user:
                                object_id = imported_user.get("ObjectId")

                        if not object_id:
                            raise RuntimeError(
                                "LDAP import returned success but user ObjectId could not be determined."
                            )

                        writer.writerow([
                            "Create User",
                            "Success",
                            f"Imported LDAP Unity user {target_alias} with extension {extension}",
                        ])
                        print(f"Imported LDAP Unity user '{target_alias}' with mailbox extension {extension}.")
                    except RuntimeError as ldap_import_error:
                        ldap_api_available = False
                        writer.writerow([
                            "Create User",
                            "Fallback",
                            f"LDAP import unavailable/failed ({ldap_import_error}); creating local Unity user",
                        ])
                        object_id = create_local_user_with_mailbox(
                            session,
                            unity_server,
                            target_alias,
                            first_name,
                            last_name,
                            display_name,
                            extension,
                            email_address,
                        )
                        print(f"Created local Unity user '{target_alias}' with mailbox extension {extension}.")
                else:
                    object_id = create_local_user_with_mailbox(
                        session,
                        unity_server,
                        target_alias,
                        first_name,
                        last_name,
                        display_name,
                        extension,
                        email_address,
                    )
                    writer.writerow(["Create User", "Success", f"Created local Unity user {target_alias} with extension {extension}"])
                    print(f"Created local Unity user '{target_alias}' with mailbox extension {extension}.")

            if object_id:
                before_ldap_payload = None
                before_export_file = None
                after_export_file = None
                diff_file = None

                if LDAP_INTEGRATION_ENABLED:
                    try:
                        before_ldap_payload = get_user_by_object_id(session, unity_server, object_id)
                        before_export_file = write_user_export(
                            target_alias,
                            "before_ldap",
                            timestamp,
                            before_ldap_payload,
                        )
                        writer.writerow([
                            "LDAP Snapshot",
                            "Success",
                            f"Before-integration export saved to {before_export_file}",
                        ])
                    except Exception as snapshot_error:
                        writer.writerow([
                            "LDAP Snapshot",
                            "Failed",
                            f"Could not export pre-LDAP snapshot: {snapshot_error}",
                        ])

                if LDAP_INTEGRATION_ENABLED and not ldap_api_available:
                    # LDAP import API unavailable — REST cannot set the checkbox.
                    # Pause and let the admin do it manually in Unity UI.
                    print(
                        "\nAction required: Open this user in Unity Connection, "
                        "check 'Integrate with LDAP Directory', then save."
                    )
                    print(f"  User alias: {target_alias}")
                    proceed_manual = input(
                        "Press Enter once you have saved the LDAP checkbox in Unity, or type 'skip' to leave unchecked: "
                    ).strip().lower()
                    if proceed_manual != "skip":
                        writer.writerow(["LDAP Integration", "Manual", "LDAP checkbox set by admin in Unity UI"])
                        print("Proceeding after manual LDAP integration step.")
                    else:
                        writer.writerow(["LDAP Integration", "Skipped", "Admin chose to skip LDAP integration"])
                        print("LDAP integration skipped.")
                elif LDAP_INTEGRATION_ENABLED:
                    try:
                        ensure_ldap_integration(session, unity_server, object_id, ldap_user_id)
                        writer.writerow(["LDAP Integration", "Success", "Mailbox is integrated with LDAP directory"])
                        print("LDAP integration confirmed.")
                    except Exception as ldap_error:
                        writer.writerow(["LDAP Integration", "Failed", str(ldap_error)])
                        raise

                if LDAP_INTEGRATION_ENABLED:
                    try:
                        after_ldap_payload = get_user_by_object_id(session, unity_server, object_id)
                        after_export_file = write_user_export(
                            target_alias,
                            "after_ldap",
                            timestamp,
                            after_ldap_payload,
                        )
                        writer.writerow([
                            "LDAP Snapshot",
                            "Success",
                            f"After-integration export saved to {after_export_file}",
                        ])

                        diff_file = write_user_diff_report(
                            target_alias,
                            timestamp,
                            before_ldap_payload,
                            after_ldap_payload,
                        )
                        writer.writerow([
                            "LDAP Diff",
                            "Success",
                            f"Diff report saved to {diff_file}",
                        ])
                        print(f"LDAP comparison report saved to: {diff_file}")
                    except Exception as snapshot_error:
                        writer.writerow([
                            "LDAP Diff",
                            "Failed",
                            f"Could not generate post-LDAP snapshot/diff: {snapshot_error}",
                        ])

                try:
                    set_user_pin(session, unity_server, object_id, pin, must_change=True)
                    writer.writerow(["Set PIN", "Success", "PIN updated and user must change at next login"])
                    print("PIN set successfully.")
                except Exception as pin_error:
                    writer.writerow(["Set PIN", "Failed", str(pin_error)])
                    print(f"PIN update failed: {pin_error}")
            else:
                writer.writerow(["Set PIN", "Skipped", "No ObjectId available to set PIN"])
                print("PIN skipped: could not determine Unity ObjectId.")

            print(f"\nVoicemail workflow complete. Results logged to: {log_file}")

        except Exception as exc:
            writer.writerow(["Script", "Error", str(exc)])
            print(f"\nScript error: {exc}")
            print(f"Results logged to: {log_file}")


if __name__ == "__main__":
    main()
