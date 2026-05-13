import csv
import datetime
import getpass
import os
import re
import urllib3
import requests
import xml.etree.ElementTree as ET
from requests.auth import HTTPBasicAuth
from xml.sax.saxutils import escape

try:
    from pyad import aduser, adquery
    PYAD_AVAILABLE = True
except ImportError:
    PYAD_AVAILABLE = False

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────
# AD PHONE FIELD HELPERS
# ─────────────────────────────────────────

def _escape_ldap_filter(value):
    """Escape LDAP filter metacharacters per RFC 4515 (backslash must be first)."""
    for char, escaped in [
        ("\\", "\\5c"),
        ("*",  "\\2a"),
        ("(",  "\\28"),
        (")",  "\\29"),
        ("\x00", "\\00"),
    ]:
        value = value.replace(char, escaped)
    return value


def _find_ad_user(samaccountname):
    """Look up AD user by sAMAccountName. Returns ADUser or None."""
    safe = _escape_ldap_filter(samaccountname)
    q = adquery.ADQuery()
    q.execute_query(
        attributes=["distinguishedName", "sAMAccountName"],
        where_clause=f"sAMAccountName = '{safe}'",
    )
    results = list(q.get_results())
    if len(results) == 1:
        return aduser.ADUser.from_dn(results[0]["distinguishedName"])
    if len(results) > 1:
        print(f"  WARNING: Multiple AD accounts matched '{samaccountname}'. Skipping.")
    return None


def clear_ad_phone_fields(samaccountname):
    """
    Remove telephoneNumber and ipPhone from the AD user account.
    Uses clear_attribute() to fully remove the values.
    Returns dict with keys: success, message.
    """
    if not PYAD_AVAILABLE:
        return {"success": False, "message": "pyad not installed (pip install pyad)"}
    try:
        user = _find_ad_user(samaccountname)
        if not user:
            return {"success": False, "message": f"AD user '{samaccountname}' not found"}
        user.clear_attribute("telephoneNumber")
        user.clear_attribute("ipPhone")
        return {"success": True, "message": "Cleared"}
    except Exception as e:
        return {"success": False, "message": str(e)}


LAB_CUCM_IP = "lascucmpl01.ahs.int"
PROD_CUCM_IP = "lascucmpp01.ahs.int"
UNITY_LAB_SERVER = "LASCUTYPL01.ahs.int"
UNITY_PROD_SERVER = "SANCUTYP01.ahs.int"
OUTPUT_DIR = "output_logs"
DEFAULT_ROUTE_PARTITION = "ENT_DEVICE_PT"
TARGET_DEVICE_PREFIXES = ("CSF", "BOT", "TCT")


def axl_post(session, cucm_ip, soap_xml):
    url = f"https://{cucm_ip}:8443/axl/"
    headers = {"Content-Type": "text/xml"}
    return session.post(url, data=soap_xml.encode("utf-8"), headers=headers, timeout=120)


def strip_ns(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def find_child(elem, tag_name):
    for child in list(elem):
        if strip_ns(child.tag) == tag_name:
            return child
    return None


def find_first_text(elem, path_candidates):
    for path in path_candidates:
        cur = elem
        found = True
        for tag_name in path:
            cur = find_child(cur, tag_name)
            if cur is None:
                found = False
                break
        if found and cur is not None and cur.text:
            value = cur.text.strip()
            if value:
                return value
    return ""


def choose_environment():
    print("\nSelect CUCM Environment:")
    print(f"  1 - LAB        ({LAB_CUCM_IP})")
    print(f"  2 - PRODUCTION ({PROD_CUCM_IP})")
    print("  0 - Return")

    while True:
        choice = input("Enter choice (0, 1, 2, LAB, or PROD): ").strip().upper()

        if choice in {"0", "R", "RETURN"}:
            return None
        if choice in {"1", "LAB"}:
            return {
                "name": "LAB",
                "cucm_ip": LAB_CUCM_IP,
                "unity_server": UNITY_LAB_SERVER,
            }
        if choice in {"2", "PROD", "PRODUCTION"}:
            return {
                "name": "PRODUCTION",
                "cucm_ip": PROD_CUCM_IP,
                "unity_server": UNITY_PROD_SERVER,
            }

        print("Invalid choice. Enter 0, 1, 2, LAB, or PROD.")


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


def parse_unity_error_text(response):
    text = (response.text or "").strip()
    if not text:
        return f"HTTP {response.status_code} with empty response body"
    return f"HTTP {response.status_code}: {text[:1200]}"


def get_user_details(session, cucm_ip, username):
    soap = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:axl=\"http://www.cisco.com/AXL/API/15.0\">
   <soapenv:Header/>
   <soapenv:Body>
      <axl:getUser>
         <userid>{escape(username)}</userid>
      </axl:getUser>
   </soapenv:Body>
</soapenv:Envelope>"""

    response = axl_post(session, cucm_ip, soap)
    if response.status_code != 200:
        raise RuntimeError(f"getUser failed with HTTP {response.status_code}: {response.text[:1000]}")

    root = ET.fromstring(response.text)
    user_node = None
    for elem in root.iter():
        if strip_ns(elem.tag) == "user":
            user_node = elem
            break

    if user_node is None:
        raise RuntimeError("Could not locate user node in getUser response.")

    associated_devices = []
    assoc_parent = find_child(user_node, "associatedDevices")
    if assoc_parent is not None:
        for child in list(assoc_parent):
            if strip_ns(child.tag) == "device" and child.text and child.text.strip():
                associated_devices.append(child.text.strip())

    primary_pattern = find_first_text(user_node, [["primaryExtension", "pattern"]])
    primary_partition = find_first_text(user_node, [["primaryExtension", "routePartitionName"]])

    return {
        "userid": find_first_text(user_node, [["userid"]]),
        "firstName": find_first_text(user_node, [["firstName"]]),
        "lastName": find_first_text(user_node, [["lastName"]]),
        "displayName": find_first_text(user_node, [["displayName"]]),
        "mailid": find_first_text(user_node, [["mailid"]]),
        "associatedDevices": associated_devices,
        "primaryExtension": {
            "pattern": primary_pattern,
            "routePartitionName": primary_partition,
        },
    }


def get_phone_details(session, cucm_ip, phone_name):
    soap = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:axl=\"http://www.cisco.com/AXL/API/15.0\">
   <soapenv:Body>
      <axl:getPhone>
         <name>{escape(phone_name)}</name>
      </axl:getPhone>
   </soapenv:Body>
</soapenv:Envelope>"""

    response = axl_post(session, cucm_ip, soap)
    if response.status_code != 200:
        raise RuntimeError(f"getPhone failed for {phone_name} with HTTP {response.status_code}: {response.text[:1000]}")

    root = ET.fromstring(response.text)
    phone_node = None
    for elem in root.iter():
        if strip_ns(elem.tag) == "phone":
            phone_node = elem
            break

    if phone_node is None:
        raise RuntimeError(f"Could not locate phone node for {phone_name}.")

    line_entries = []
    lines_parent = find_child(phone_node, "lines")
    if lines_parent is not None:
        for line in list(lines_parent):
            if strip_ns(line.tag) != "line":
                continue
            index = find_first_text(line, [["index"]])
            pattern = find_first_text(line, [["dirn", "pattern"]])
            partition = find_first_text(line, [["dirn", "routePartitionName"]])
            if pattern:
                line_entries.append({
                    "index": index,
                    "pattern": pattern,
                    "partition": partition,
                })

    owner_user_name = find_first_text(phone_node, [["ownerUserName"]])
    description = find_first_text(phone_node, [["description"]])

    return {
        "name": phone_name,
        "ownerUserName": owner_user_name,
        "description": description,
        "lines": line_entries,
    }


def get_line_state(session, cucm_ip, pattern, route_partition):
    soap = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:axl=\"http://www.cisco.com/AXL/API/15.0\">
   <soapenv:Body>
      <axl:getLine>
         <pattern>{escape(pattern)}</pattern>
         <routePartitionName>{escape(route_partition)}</routePartitionName>
         <returnedTags>
            <pattern/>
            <routePartitionName/>
            <active/>
            <description/>
            <associatedDevices>
               <device/>
            </associatedDevices>
         </returnedTags>
      </axl:getLine>
   </soapenv:Body>
</soapenv:Envelope>"""

    response = axl_post(session, cucm_ip, soap)
    if response.status_code != 200:
        return {
            "found": False,
            "active": "",
            "description": "",
            "associatedDevices": [],
        }

    root = ET.fromstring(response.text)
    line_node = None
    for elem in root.iter():
        if strip_ns(elem.tag) == "line":
            line_node = elem
            break

    if line_node is None:
        return {
            "found": False,
            "active": "",
            "description": "",
            "associatedDevices": [],
        }

    assoc_devices = []
    assoc_parent = find_child(line_node, "associatedDevices")
    if assoc_parent is not None:
        for child in list(assoc_parent):
            if strip_ns(child.tag) == "device" and child.text and child.text.strip():
                assoc_devices.append(child.text.strip())

    return {
        "found": True,
        "active": find_first_text(line_node, [["active"]]),
        "description": find_first_text(line_node, [["description"]]),
        "associatedDevices": assoc_devices,
    }


def sanitize_cucm_line_description(raw_text, max_len=50):
    if not raw_text:
        return ""

    disallowed_chars = {'"', "%", "&", "<", ">"}
    cleaned = "".join(ch for ch in str(raw_text) if ord(ch) >= 32 and ch not in disallowed_chars)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned.strip()


def build_update_user_devices_soap(
    userid,
    associated_devices,
    removed_phone,
    removed_dn,
    removed_partition,
    removed_line_description,
    clear_self_service=True,
    clear_service_profile=True,
    clear_associated_groups=True,
):
    device_xml = "\n".join(
        f"            <device>{escape(device)}</device>" for device in associated_devices
    )

    presence_remove_xml = ""
    if removed_phone and removed_dn and removed_partition:
        presence_remove_xml = (
            "         <lineAppearanceAssociationForPresences>\n"
            "            <lineAppearanceAssociationForPresence>\n"
            "               <laapAssociate>f</laapAssociate>\n"
            "               <laapProductType>Cisco Unified Client Services Framework</laapProductType>\n"
            f"               <laapDeviceName>{escape(removed_phone)}</laapDeviceName>\n"
            f"               <laapDirectory>{escape(removed_dn)}</laapDirectory>\n"
            f"               <laapPartition>{escape(removed_partition)}</laapPartition>\n"
            f"               <laapDescription>{escape(removed_line_description)}</laapDescription>\n"
            "            </lineAppearanceAssociationForPresence>\n"
            "         </lineAppearanceAssociationForPresences>\n"
        )

    clear_fields_xml = ""
    if clear_self_service:
        clear_fields_xml += "            <selfService></selfService>\n"
    clear_fields_xml += "            <homeCluster>f</homeCluster>\n"
    if clear_service_profile:
        clear_fields_xml += "            <serviceProfile></serviceProfile>\n"
    clear_fields_xml += "            <primaryExtension/>\n"
    if clear_associated_groups:
        clear_fields_xml += "            <associatedGroups/>\n"

    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:axl=\"http://www.cisco.com/AXL/API/15.0\">
    <soapenv:Body>
        <axl:updateUser>
            <userid>{escape(userid)}</userid>
            <associatedDevices>
{device_xml}
            </associatedDevices>
{clear_fields_xml}
{presence_remove_xml}      </axl:updateUser>
    </soapenv:Body>
</soapenv:Envelope>"""


def build_update_line_inactive_soap(pattern, route_partition):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
    <soapenv:Body>
        <axl:updateLine>
            <pattern>{escape(pattern)}</pattern>
            <routePartitionName>{escape(route_partition)}</routePartitionName>
            <alertingName></alertingName>
            <asciiAlertingName></asciiAlertingName>
            <active>false</active>
        </axl:updateLine>
    </soapenv:Body>
</soapenv:Envelope>"""


def choose_fallback_primary_extension(session, cucm_ip, device_names, removed_dn, removed_partition):
    for device_name in device_names:
        try:
            details = get_phone_details(session, cucm_ip, device_name)
        except Exception:
            continue

        for line in details.get("lines", []):
            pattern = (line.get("pattern") or "").strip()
            partition = (line.get("partition") or "").strip() or DEFAULT_ROUTE_PARTITION
            if not pattern:
                continue
            if pattern == removed_dn and partition == removed_partition:
                continue
            return {
                "pattern": pattern,
                "partition": partition,
            }

    return {
        "pattern": "",
        "partition": "",
    }


def build_delete_phone_soap(phone_name):
    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:axl=\"http://www.cisco.com/AXL/API/15.0\">
   <soapenv:Body>
      <axl:removePhone>
         <name>{escape(phone_name)}</name>
      </axl:removePhone>
   </soapenv:Body>
</soapenv:Envelope>"""





def get_unity_user_by_alias(session, unity_server, alias):
    query = f"(Alias is {alias})"
    url = make_unity_url(unity_server, "/vmrest/users")
    response = session.get(url, headers=unity_headers(), params={"query": query}, timeout=120)

    if response.status_code != 200:
        raise RuntimeError(f"Unity user lookup failed: {parse_unity_error_text(response)}")

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


def delete_unity_user_by_object_id(session, unity_server, object_id):
    url = make_unity_url(unity_server, f"/vmrest/users/{object_id}")
    response = session.delete(url, headers=unity_headers(), timeout=120)
    if response.status_code not in {200, 202, 204}:
        raise RuntimeError(f"Unity mailbox delete failed: {parse_unity_error_text(response)}")


def confirm_yes_no(prompt, default_no=True):
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    value = input(prompt + suffix).strip().lower()
    if not value:
        return not default_no
    return value in {"y", "yes"}


def run_decommission_for_user(cucm_ip, unity_server, cucm_user, cucm_pass, target_user):
    current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log_file = os.path.join(OUTPUT_DIR, f"decommission_user_csf_{target_user}_{current_time}.csv")

    session = requests.Session()
    session.verify = False
    session.auth = HTTPBasicAuth(cucm_user, cucm_pass)

    unity_session = requests.Session()
    unity_session.verify = False
    unity_session.auth = HTTPBasicAuth(cucm_user, cucm_pass)

    with open(log_file, "w", newline="", encoding="utf-8") as log_handle:
        log_writer = csv.writer(log_handle)
        log_writer.writerow(["Step", "Status", "Details"])

        try:
            user_details = get_user_details(session, cucm_ip, target_user)
            display_name = " ".join(
                p for p in [user_details.get("firstName", ""), user_details.get("lastName", "")] if p
            ).strip() or user_details.get("displayName", "") or target_user

            log_writer.writerow(["Lookup User", "Success", f"Found {target_user} ({display_name})"])
            print(f"\nFound user: {target_user} ({display_name})")

            target_device_names = [
                d
                for d in user_details.get("associatedDevices", [])
                if d and d.upper().startswith(TARGET_DEVICE_PREFIXES)
            ]

            if not target_device_names:
                log_writer.writerow([
                    "Find Devices",
                    "Skipped",
                    "No CSF/BOT/TCT devices associated to user",
                ])
                print("No CSF/BOT/TCT devices found in associatedDevices for this user.")
                return log_file

            phone_candidates = []
            for device_name in target_device_names:
                try:
                    details = get_phone_details(session, cucm_ip, device_name)
                except Exception as phone_err:
                    log_writer.writerow(["Get Phone", "Failed", f"{device_name}: {phone_err}"])
                    continue

                primary_line = details["lines"][0] if details["lines"] else {
                    "pattern": "",
                    "partition": DEFAULT_ROUTE_PARTITION,
                }
                phone_candidates.append(
                    {
                        "name": details["name"],
                        "primary_line": primary_line,
                        "all_lines": details["lines"],
                    }
                )

            if not phone_candidates:
                log_writer.writerow(["Get Phone", "Failed", "Could not fetch any CSF/BOT/TCT phone details"])
                print("Could not fetch CSF/BOT/TCT phone details for this user.")
                return log_file

            print("\nTarget devices for removal:")
            for phone in phone_candidates:
                primary_line = phone.get("primary_line", {})
                dn_pattern = (primary_line.get("pattern") or "").strip()
                dn_partition = (primary_line.get("partition") or "").strip() or DEFAULT_ROUTE_PARTITION
                print(f"  - {phone['name']} | DN: {dn_pattern or '<not found>'} | PT: {dn_partition}")

            dn_targets = []
            seen_dn_keys = set()
            for phone in phone_candidates:
                for line in phone.get("all_lines", []):
                    dn_pattern = (line.get("pattern") or "").strip()
                    dn_partition = (line.get("partition") or "").strip() or DEFAULT_ROUTE_PARTITION
                    if not dn_pattern:
                        continue
                    dn_key = (dn_pattern, dn_partition)
                    if dn_key in seen_dn_keys:
                        continue
                    seen_dn_keys.add(dn_key)
                    dn_targets.append({"pattern": dn_pattern, "partition": dn_partition})

            if dn_targets:
                print("DN(s) to mark inactive:")
                for dn_item in dn_targets:
                    print(f"  - {dn_item['pattern']} / {dn_item['partition']}")
            else:
                print("No line DN found on matched devices.")

            proceed = confirm_yes_no(
                "Proceed to UNDO build (End User rollback, remove CSF/BOT/TCT, remove Unity mailbox, mark DN inactive)?",
                default_no=True,
            )
            if not proceed:
                log_writer.writerow(["Confirmation", "Cancelled", "Operator cancelled before changes"])
                print("Cancelled by operator.")
                return log_file

            removed_phone_names = [phone["name"] for phone in phone_candidates]
            updated_devices = [d for d in user_details.get("associatedDevices", []) if d not in removed_phone_names]

            first_csf_phone = next(
                (phone for phone in phone_candidates if phone.get("name", "").upper().startswith("CSF")),
                None,
            )
            removed_phone_for_presence = first_csf_phone["name"] if first_csf_phone else ""
            removed_line_description = (
                (first_csf_phone.get("description", "").strip() if first_csf_phone else "")
                or f"CSF {display_name}"
            )
            removed_dn_for_presence = ""
            removed_partition_for_presence = ""
            if first_csf_phone:
                removed_dn_for_presence = (first_csf_phone["primary_line"].get("pattern") or "").strip()
                removed_partition_for_presence = (
                    (first_csf_phone["primary_line"].get("partition") or "").strip() or DEFAULT_ROUTE_PARTITION
                )

            update_user_soap = build_update_user_devices_soap(
                user_details["userid"],
                updated_devices,
                removed_phone_for_presence,
                removed_dn_for_presence,
                removed_partition_for_presence,
                removed_line_description,
            )
            user_update_resp = axl_post(session, cucm_ip, update_user_soap)
            if user_update_resp.status_code != 200 and "Item not valid: The specified" in (user_update_resp.text or ""):
                retry_user_soap = build_update_user_devices_soap(
                    user_details["userid"],
                    updated_devices,
                    removed_phone_for_presence,
                    removed_dn_for_presence,
                    removed_partition_for_presence,
                    removed_line_description,
                    clear_self_service=True,
                    clear_service_profile=False,
                    clear_associated_groups=False,
                )
                retry_user_resp = axl_post(session, cucm_ip, retry_user_soap)
                if retry_user_resp.status_code == 200:
                    user_update_resp = retry_user_resp
                    log_writer.writerow([
                        "Update User",
                        "Info",
                        "Retried updateUser without serviceProfile/associatedGroups clear due to CUCM validation fault",
                    ])
            if user_update_resp.status_code == 200:
                log_writer.writerow([
                    "Update User",
                    "Success",
                    (
                        f"Rolled back End User entries for {user_details['userid']}: "
                        f"removed devices {', '.join(removed_phone_names)}, removed CSF line presence mapping when available, "
                        f"cleared Primary Extension, UC Service Profile, and Roles"
                    ),
                ])
                print("End User rollback completed.")

                refreshed_user = get_user_details(session, cucm_ip, user_details["userid"])
                refreshed_primary = refreshed_user.get("primaryExtension", {})
                refreshed_primary_pattern = (refreshed_primary.get("pattern") or "").strip()
                refreshed_primary_partition = (refreshed_primary.get("routePartitionName") or "").strip()
                removed_dn_keys = {(item["pattern"], item["partition"]) for item in dn_targets}
                if (refreshed_primary_pattern, refreshed_primary_partition) in removed_dn_keys:
                    log_writer.writerow([
                        "Verify End User Primary",
                        "Warning",
                        (
                            "Primary extension still points to one of the removed DNs. "
                            "If this is the user's only line, clear/adjust primary extension manually in CUCM End User."
                        ),
                    ])
                    print("Warning: primary extension still points to a removed DN; review End User primary extension.")
                else:
                    log_writer.writerow([
                        "Verify End User Primary",
                        "Success",
                        f"Primary extension now {refreshed_primary_pattern}/{refreshed_primary_partition}",
                    ])
            else:
                log_writer.writerow([
                    "Update User",
                    "Failed",
                    f"HTTP {user_update_resp.status_code}: {user_update_resp.text[:1000]}",
                ])
                print(f"Update User failed with HTTP {user_update_resp.status_code}.")

            removed_count = 0
            for phone in phone_candidates:
                delete_phone_soap = build_delete_phone_soap(phone["name"])
                delete_phone_resp = axl_post(session, cucm_ip, delete_phone_soap)
                if delete_phone_resp.status_code == 200:
                    removed_count += 1
                    log_writer.writerow(["Remove Phone", "Success", f"Removed phone {phone['name']}"])
                    print(f"Removed phone {phone['name']}.")
                else:
                    log_writer.writerow([
                        "Remove Phone",
                        "Failed",
                        f"{phone['name']} -> HTTP {delete_phone_resp.status_code}: {delete_phone_resp.text[:1000]}",
                    ])
                    print(f"Remove Phone failed for {phone['name']} with HTTP {delete_phone_resp.status_code}.")

            log_writer.writerow([
                "Device Summary",
                "Info",
                f"Found {len(phone_candidates)} target device(s); removed {removed_count}",
            ])

            unity_deleted = False
            try:
                unity_user = get_unity_user_by_alias(unity_session, unity_server, user_details["userid"])
                if unity_user and unity_user.get("ObjectId"):
                    delete_unity_user_by_object_id(unity_session, unity_server, unity_user["ObjectId"])
                    unity_deleted = True
                    log_writer.writerow([
                        "Delete Unity Mailbox",
                        "Success",
                        f"Deleted Unity mailbox alias {user_details['userid']} on {unity_server}",
                    ])
                    print("Deleted Unity mailbox.")
                else:
                    log_writer.writerow([
                        "Delete Unity Mailbox",
                        "Skipped",
                        f"No Unity mailbox found for alias {user_details['userid']}",
                    ])
                    print("No Unity mailbox found for alias.")
            except Exception as unity_err:
                log_writer.writerow(["Delete Unity Mailbox", "Failed", str(unity_err)])
                print(f"Unity mailbox delete failed: {unity_err}")

            if dn_targets:
                for dn_item in dn_targets:
                    pattern = dn_item["pattern"]
                    partition = dn_item["partition"]
                    update_line_soap = build_update_line_inactive_soap(pattern, partition)
                    line_resp = axl_post(session, cucm_ip, update_line_soap)

                    if line_resp.status_code == 200:
                        log_writer.writerow([
                            "Update Line Inactive",
                            "Success",
                            f"Marked {pattern}/{partition} inactive and reusable",
                        ])
                        print(f"Marked DN {pattern} inactive for reuse.")
                    else:
                        log_writer.writerow([
                            "Update Line Inactive",
                            "Failed",
                            f"{pattern}/{partition} -> HTTP {line_resp.status_code}: {line_resp.text[:1000]}",
                        ])
                        print(f"Update Line failed for {pattern}/{partition} with HTTP {line_resp.status_code}.")

                    line_state = get_line_state(session, cucm_ip, pattern, partition)
                    if line_state["found"]:
                        summary = (
                            f"{pattern}/{partition}: active={line_state['active']}; "
                            f"associatedDevices={len(line_state['associatedDevices'])}; "
                            f"description={line_state['description']}"
                        )
                        log_writer.writerow(["Verify Line", "Success", summary])
                        print(f"Line verification: {summary}")
                    else:
                        log_writer.writerow([
                            "Verify Line",
                            "Failed",
                            f"Could not read line state after update for {pattern}/{partition}",
                        ])
            else:
                log_writer.writerow([
                    "Update Line Inactive",
                    "Skipped",
                    "No DN found on matched devices",
                ])
                print("No DN found on matched devices; line update skipped.")

            # ── Clear AD phone fields ────────────────────────────
            try:
                ad_result = clear_ad_phone_fields(user_details["userid"])
                if ad_result["success"]:
                    log_writer.writerow(["AD Clear", "Success", "telephoneNumber and ipPhone cleared"])
                    print("✓ AD fields cleared: telephoneNumber, ipPhone")
                else:
                    log_writer.writerow(["AD Clear", "Warning", ad_result["message"]])
                    print(f"⚠ AD clear skipped: {ad_result['message']}")
            except Exception as ad_err:
                log_writer.writerow(["AD Clear", "Error", str(ad_err)])
                print(f"⚠ AD clear error: {ad_err}")

            if unity_deleted:
                print("\nOffboarding complete: CSF/BOT/TCT removed, Unity mailbox removed, DN retained as inactive.")
            else:
                print("\nOffboarding complete with Unity mailbox skipped/failed. Review log for details.")

        except Exception as e:
            log_writer.writerow(["Script", "Error", str(e)])
            print(f"Script failed: {e}")

    return log_file


def main():
    print("============================================================")
    print(" CUCM/Unity Offboarding - Remove CSF/BOT/TCT + Mailbox, Keep DN")
    print("============================================================")

    env = choose_environment()
    if env is None:
        return

    if env["name"] == "PRODUCTION":
        proceed_prod = confirm_yes_no(
            "You selected PRODUCTION. Continue with live offboarding actions?",
            default_no=True,
        )
        if not proceed_prod:
            print("Cancelled.")
            return

    cucm_ip = env["cucm_ip"]
    unity_server = env["unity_server"]
    print(f"Using {env['name']} CUCM: {cucm_ip}")
    print(f"Using {env['name']} Unity: {unity_server}")

    cucm_user = input("Enter CUCM Username: ").strip()
    cucm_pass = getpass.getpass("Enter CUCM Password: ")

    while True:
        target_user = input("Enter userid to offboard (e.g., first.last): ").strip()
        if not target_user:
            print("No userid entered. Exiting.")
            return

        log_file = run_decommission_for_user(cucm_ip, unity_server, cucm_user, cucm_pass, target_user)
        print(f"\nResults logged to: {log_file}")

        run_another = confirm_yes_no(
            "Run another decommission using the same username/password?",
            default_no=True,
        )
        if not run_another:
            break


if __name__ == "__main__":
    main()
