import csv
import datetime
import getpass
import os
import urllib3
import requests
import xml.etree.ElementTree as ET
from requests.auth import HTTPBasicAuth
from xml.sax.saxutils import escape

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LAB_CUCM_IP = "lascucmpl01.ahs.int"
PROD_CUCM_IP = "lascucmpp01.ahs.int"
OUTPUT_DIR = "output_logs"
TARGET_DEVICE_PREFIX = "CSF"


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
    print(f"  1 - PRODUCTION ({PROD_CUCM_IP})")
    print(f"  2 - LAB        ({LAB_CUCM_IP})")
    while True:
        choice = input("Enter choice (1 or 2): ").strip()
        if choice == "1":
            return "PRODUCTION", PROD_CUCM_IP
        if choice == "2":
            return "LAB", LAB_CUCM_IP
        print("Invalid choice. Please enter 1 or 2.")


def get_user_details(session, cucm_ip, userid):
    soap = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:axl=\"http://www.cisco.com/AXL/API/15.0\">
   <soapenv:Header/>
   <soapenv:Body>
      <axl:getUser>
         <userid>{escape(userid)}</userid>
      </axl:getUser>
   </soapenv:Body>
</soapenv:Envelope>"""

    response = axl_post(session, cucm_ip, soap)
    if response.status_code != 200:
        return {
            "success": False,
            "error": f"getUser HTTP {response.status_code}: {response.text[:500]}",
        }

    try:
        root = ET.fromstring(response.text)
    except Exception as exc:
        return {"success": False, "error": f"getUser XML parse error: {exc}"}

    user_node = None
    for elem in root.iter():
        if strip_ns(elem.tag) == "user":
            user_node = elem
            break

    if user_node is None:
        return {"success": False, "error": "No user node in getUser response"}

    associated_devices = []
    assoc_parent = find_child(user_node, "associatedDevices")
    if assoc_parent is not None:
        for child in list(assoc_parent):
            if strip_ns(child.tag) == "device" and child.text and child.text.strip():
                associated_devices.append(child.text.strip())

    return {
        "success": True,
        "userid": find_first_text(user_node, [["userid"]]) or userid,
        "displayName": find_first_text(user_node, [["displayName"]]),
        "firstName": find_first_text(user_node, [["firstName"]]),
        "lastName": find_first_text(user_node, [["lastName"]]),
        "associatedDevices": associated_devices,
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
        return {"success": False, "error": f"getPhone HTTP {response.status_code}: {response.text[:500]}"}

    try:
        root = ET.fromstring(response.text)
    except Exception as exc:
        return {"success": False, "error": f"getPhone XML parse error: {exc}"}

    phone_node = None
    for elem in root.iter():
        if strip_ns(elem.tag) == "phone":
            phone_node = elem
            break

    if phone_node is None:
        return {"success": False, "error": "No phone node in getPhone response"}

    lines = []
    lines_parent = find_child(phone_node, "lines")
    if lines_parent is not None:
        for line_node in list(lines_parent):
            if strip_ns(line_node.tag) != "line":
                continue
            line_position = find_first_text(line_node, [["index"]])
            directory_number = find_first_text(line_node, [["dirn", "pattern"], ["pattern"]])
            line_description = find_first_text(line_node, [["description"], ["dirn", "description"]])
            line_text_label = find_first_text(line_node, [["label"], ["lineTextLabel"]])
            external_mask = find_first_text(line_node, [["e164Mask"], ["externalPhoneNumberMask"]])

            if directory_number or line_description or line_text_label or external_mask:
                lines.append(
                    {
                        "line_position": line_position,
                        "directory_number": directory_number,
                        "description": line_description,
                        "line_text_label": line_text_label,
                        "external_phone_number_mask": external_mask,
                    }
                )

    return {
        "success": True,
        "name": find_first_text(phone_node, [["name"]]) or phone_name,
        "description": find_first_text(phone_node, [["description"]]),
        "product": find_first_text(phone_node, [["product"]]),
        "protocol": find_first_text(phone_node, [["protocol"]]),
        "class": find_first_text(phone_node, [["class"]]),
        "ownerUserName": find_first_text(phone_node, [["ownerUserName"]]),
        "lines": lines,
    }


def collect_userids_one_by_one():
    print("\nEnter each userid to extract, one by one.")
    print("Press Enter on a blank line when done. Type 0 to cancel.")

    userids = []
    seen = set()

    while True:
        user_input = input("  Userid: ").strip()
        if not user_input:
            break
        if user_input == "0":
            return []
        key = user_input.lower()
        if key in seen:
            print("    (Already added; skipping duplicate)")
            continue
        seen.add(key)
        userids.append(user_input)

    return userids


def sort_lines_for_export(lines):
    def sort_key(line):
        raw_position = (line.get("line_position") or "").strip()
        if raw_position.isdigit():
            return (0, int(raw_position), line.get("directory_number") or "")
        return (1, raw_position, line.get("directory_number") or "")

    return sorted(lines, key=sort_key)


def main():
    print("==================================================")
    print("  CUCM AXL - Extract RPO Phones (v1)")
    print("==================================================")

    env_name, cucm_ip = choose_environment()
    print(f"Using {env_name} CUCM: {cucm_ip}")

    cucm_user = input("Enter CUCM Username: ").strip()
    cucm_pass = getpass.getpass("Enter CUCM Password: ")

    userids = collect_userids_one_by_one()
    if not userids:
        print("No users entered. Exiting.")
        return

    session = requests.Session()
    session.verify = False
    session.auth = HTTPBasicAuth(cucm_user, cucm_pass)

    rows = []
    total_lines_extracted = 0
    max_lines_for_any_user = 0

    for idx, userid in enumerate(userids, start=1):
        print(f"\n[{idx}/{len(userids)}] Processing user: {userid}")

        user_result = get_user_details(session, cucm_ip, userid)
        if not user_result["success"]:
            print(f"  ✗ User lookup failed: {user_result['error']}")
            continue

        associated_devices = [
            device_name
            for device_name in user_result.get("associatedDevices", [])
            if device_name.upper().startswith(TARGET_DEVICE_PREFIX)
        ]
        if not associated_devices:
            print("  ! No CSF devices found for this user")
            continue

        print(f"  Found {len(associated_devices)} CSF device(s)")

        collected_lines = []

        for device_name in associated_devices:
            phone_result = get_phone_details(session, cucm_ip, device_name)
            if not phone_result["success"]:
                print(f"    ✗ {device_name}: {phone_result['error']}")
                continue

            lines = phone_result.get("lines", [])
            if not lines:
                print(f"    ! {device_name}: no line data returned")
                continue

            collected_lines.extend(lines)
            total_lines_extracted += len(lines)

            print(f"    ✓ {device_name}: {len(lines)} line(s)")

        if not collected_lines:
            continue

        sorted_lines = sort_lines_for_export(collected_lines)
        max_lines_for_any_user = max(max_lines_for_any_user, len(sorted_lines))

        row = {"Username": user_result["userid"]}
        for line_number, line in enumerate(sorted_lines, start=1):
            row[f"Line {line_number} Position"] = line.get("line_position", "")
            row[f"Line {line_number} Directory Number"] = line.get("directory_number", "")
            row[f"Line {line_number} Description"] = line.get("description", "")
            row[f"Line {line_number} Line Text Label"] = line.get("line_text_label", "")
            row[f"Line {line_number} External Phone Number Mask"] = line.get("external_phone_number_mask", "")
        rows.append(row)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(OUTPUT_DIR, f"extract_rpo_phones_{timestamp}.csv")

    fieldnames = ["Username"]
    for line_number in range(1, max_lines_for_any_user + 1):
        fieldnames.extend(
            [
                f"Line {line_number} Position",
                f"Line {line_number} Directory Number",
                f"Line {line_number} Description",
                f"Line {line_number} Line Text Label",
                f"Line {line_number} External Phone Number Mask",
            ]
        )

    with open(output_file, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print("\n==================================================")
    print("Export complete")
    print(f"Users entered       : {len(userids)}")
    print(f"Rows written        : {len(rows)}")
    print(f"Total lines found   : {total_lines_extracted}")
    print(f"Output file         : {output_file}")
    print("==================================================")


if __name__ == "__main__":
    main()
