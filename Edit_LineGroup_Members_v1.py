import urllib3
import requests
from requests.auth import HTTPBasicAuth
import csv
import getpass
import os
import datetime
import xml.etree.ElementTree as ET

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def axl_post(session, cucm_ip, soap_xml):
    url = f"https://{cucm_ip}:8443/axl/"
    headers = {"Content-Type": "text/xml"}
    return session.post(url, data=soap_xml.encode("utf-8"), headers=headers, timeout=120)


def xml_escape(value):
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def lookup_alerting_name(session, cucm_ip, pattern, partition):
    soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:getLine>
         <pattern>{xml_escape(pattern)}</pattern>
         <routePartitionName>{xml_escape(partition)}</routePartitionName>
         <returnedTags>
            <alertingName/>
            <asciiAlertingName/>
            <description/>
         </returnedTags>
      </axl:getLine>
   </soapenv:Body>
</soapenv:Envelope>"""

    try:
        resp = axl_post(session, cucm_ip, soap)
    except Exception:
        return ""

    if resp.status_code != 200:
        return ""

    try:
        root = ET.fromstring(resp.text)
    except Exception:
        return ""

    alert_elem = root.find('.//{*}alertingName')
    if alert_elem is not None and alert_elem.text and alert_elem.text.strip():
        return alert_elem.text.strip()

    ascii_elem = root.find('.//{*}asciiAlertingName')
    if ascii_elem is not None and ascii_elem.text and ascii_elem.text.strip():
        return ascii_elem.text.strip()

    desc_elem = root.find('.//{*}description')
    if desc_elem is not None and desc_elem.text and desc_elem.text.strip():
        return desc_elem.text.strip()

    return ""


def list_line_groups(session, cucm_ip, search_text):
    search_name = f"%{search_text}%" if search_text else "%"

    soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:listLineGroup>
         <searchCriteria>
            <name>{xml_escape(search_name)}</name>
         </searchCriteria>
         <returnedTags>
            <name/>
         </returnedTags>
      </axl:listLineGroup>
   </soapenv:Body>
</soapenv:Envelope>"""

    resp = axl_post(session, cucm_ip, soap)
    if resp.status_code != 200:
        raise RuntimeError(f"listLineGroup failed: HTTP {resp.status_code}")

    root = ET.fromstring(resp.text)
    names = []
    for lg in root.findall('.//{*}lineGroup'):
        n = lg.find('{*}name')
        if n is not None and n.text:
            names.append(n.text.strip())

    names = sorted(set(names), key=lambda s: s.lower())
    return names


def get_line_group_members(session, cucm_ip, line_group_name):
    soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:getLineGroup>
         <name>{xml_escape(line_group_name)}</name>
      </axl:getLineGroup>
   </soapenv:Body>
</soapenv:Envelope>"""

    resp = axl_post(session, cucm_ip, soap)
    if resp.status_code != 200:
        raise RuntimeError(f"getLineGroup failed: HTTP {resp.status_code}")

    root = ET.fromstring(resp.text)
    members = []

    for member in root.findall('.//{*}members/{*}member'):
        dirn = member.find('{*}directoryNumber')
        if dirn is None:
            dirn = member.find('{*}dirn')

        if dirn is None:
            continue

        pattern_elem = dirn.find('{*}pattern')
        part_elem = dirn.find('{*}routePartitionName')

        pattern = pattern_elem.text.strip() if pattern_elem is not None and pattern_elem.text else ""
        partition = part_elem.text.strip() if part_elem is not None and part_elem.text else ""

        if pattern:
            members.append((pattern, partition))

    if not members:
        for dirn in root.findall('.//{*}dirn'):
            pattern_elem = dirn.find('{*}pattern')
            part_elem = dirn.find('{*}routePartitionName')
            pattern = pattern_elem.text.strip() if pattern_elem is not None and pattern_elem.text else ""
            partition = part_elem.text.strip() if part_elem is not None and part_elem.text else ""
            if pattern:
                members.append((pattern, partition))

    deduped = []
    seen = set()
    for item in members:
        if item not in seen:
            seen.add(item)
            deduped.append(item)

    return deduped


def update_line_group_members(session, cucm_ip, line_group_name, members):
    if not members:
        return False, "Cannot update line group with zero members via this script."

    member_xml_parts = []
    for pattern, partition in members:
        member_xml_parts.append(
            """
            <member>
               <directoryNumber>
                  <pattern>{pattern}</pattern>
                  <routePartitionName>{partition}</routePartitionName>
               </directoryNumber>
            </member>
            """.format(pattern=xml_escape(pattern), partition=xml_escape(partition))
        )

    members_xml = "\n".join(member_xml_parts)

    soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:updateLineGroup>
         <name>{xml_escape(line_group_name)}</name>
         <members>
{members_xml}
         </members>
      </axl:updateLineGroup>
   </soapenv:Body>
</soapenv:Envelope>"""

    resp = axl_post(session, cucm_ip, soap)
    if resp.status_code == 200:
        return True, "Updated successfully"

    return False, f"HTTP {resp.status_code}: {resp.text[:500].replace(chr(10), ' ')}"


print("==================================================")
print("  CUCM AXL - Edit Line Group Members (v1)")
print("==================================================\n")

# Ask for credentials
cucm_user = input("Enter CUCM Username: ")
cucm_pass = getpass.getpass("Enter CUCM Password: ")

session = requests.Session()
session.verify = False
session.auth = HTTPBasicAuth(cucm_user, cucm_pass)

# Ask which CUCM environment to use
print("\nSelect CUCM Environment:")
print("  1 - PRODUCTION (lascucmpp01.ahs.int)")
print("  2 - LAB        (lascucmpl01.ahs.int)")
cucm_choice = input("Enter choice (1 or 2): ").strip()
if cucm_choice == '1':
    CUCM_IP = 'lascucmpp01.ahs.int'
    print("Using PRODUCTION CUCM")
else:
    CUCM_IP = 'lascucmpl01.ahs.int'
    print("Using LAB CUCM")

print("\nSelect Action:")
print("  1 - Add number to Line Group")
print("  2 - Remove number from Line Group")
action_choice = input("Enter choice (1 or 2): ").strip()

if action_choice == '1':
    action = 'ADD'
elif action_choice == '2':
    action = 'REMOVE'
else:
    print("Invalid action selected. Exiting.")
    exit(1)

partial_name = input("\nEnter full or partial Line Group name: ").strip()
if not partial_name:
    print("Line Group search text is required. Exiting.")
    exit(1)

print("\nSearching for matching Line Groups...")
try:
    matches = list_line_groups(session, CUCM_IP, partial_name)
except Exception as e:
    print(f"✗ Could not search line groups: {e}")
    exit(1)

if not matches:
    print("No matching Line Groups found. Exiting.")
    exit(0)

print(f"Found {len(matches)} match(es):")
for idx, name in enumerate(matches, start=1):
    print(f"  {idx}. {name}")

selection_raw = input(f"\nSelect Line Group to edit (1-{len(matches)}): ").strip()
if not selection_raw.isdigit():
    print("Invalid selection. Exiting.")
    exit(1)

selection = int(selection_raw)
if selection < 1 or selection > len(matches):
    print("Selection out of range. Exiting.")
    exit(1)

line_group_name = matches[selection - 1]
print(f"Selected Line Group: {line_group_name}")

try:
    members = get_line_group_members(session, CUCM_IP, line_group_name)
except Exception as e:
    print(f"✗ Could not read current members: {e}")
    exit(1)

alerting_cache = {}

print("\nCurrent Line Group Members:")
if members:
    print(f"{'Extension':<15} {'Route Partition':<35} Alerting Name")
    print("-" * 90)
    for pattern, partition in members:
        key = f"{pattern}|{partition}"
        if key not in alerting_cache:
            alerting_cache[key] = lookup_alerting_name(session, CUCM_IP, pattern, partition)
        print(f"{pattern:<15} {partition:<35} {alerting_cache[key]}")
else:
    print("(No members found)")

target_pattern = input("Enter Directory Number pattern (extension): ").strip()
target_partition = input("Enter Route Partition name: ").strip()

if not target_pattern or not target_partition:
    print("Pattern and Route Partition are both required. Exiting.")
    exit(1)

confirm = input(
    f"\nConfirm {action} {target_pattern} ({target_partition}) in '{line_group_name}'? (Y/N): "
).strip().lower()
if confirm not in ('y', 'yes'):
    print("Cancelled by user. Exiting.")
    exit(0)

current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
output_dir = 'output_logs'
os.makedirs(output_dir, exist_ok=True)
log_filename = os.path.join(output_dir, f"edit_line_group_members_{current_time}.csv")

status = ""
details = ""

try:
    before_count = len(members)
    target = (target_pattern, target_partition)

    if action == 'ADD':
        if target in members:
            status = "Skipped"
            details = "Directory Number is already a member of the Line Group"
            after_count = before_count
        else:
            members.append(target)
            success, result = update_line_group_members(session, CUCM_IP, line_group_name, members)
            status = "Success" if success else "Failed"
            details = result
            after_count = len(members) if success else before_count
    else:
        if target not in members:
            status = "Skipped"
            details = "Directory Number was not found in the Line Group"
            after_count = before_count
        else:
            new_members = [m for m in members if m != target]
            if not new_members:
                status = "Skipped"
                details = "Cannot remove the last member from the Line Group with this script"
                after_count = before_count
            else:
                success, result = update_line_group_members(session, CUCM_IP, line_group_name, new_members)
                status = "Success" if success else "Failed"
                details = result
                after_count = len(new_members) if success else before_count

except Exception as e:
    status = "Error"
    details = str(e)
    before_count = 0
    after_count = 0

with open(log_filename, 'w', newline='', encoding='utf-8') as logfile:
    log_writer = csv.writer(logfile)
    log_writer.writerow([
        'Timestamp',
        'Action',
        'Line Group Name',
        'Pattern',
        'Route Partition',
        'Status',
        'Details',
        'Members Before',
        'Members After'
    ])
    log_writer.writerow([
        current_time,
        action,
        line_group_name,
        target_pattern,
        target_partition,
        status,
        details,
        before_count,
        after_count
    ])

if status == "Success":
    print("\n✓ Line Group update completed successfully.")
elif status == "Skipped":
    print(f"\n- No change applied: {details}")
else:
    print(f"\n✗ Update failed: {details}")

print(f"Log file: {log_filename}")
