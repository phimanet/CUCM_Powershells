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
    get_line_soap = f"""<?xml version="1.0" encoding="UTF-8"?>
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
        resp = axl_post(session, cucm_ip, get_line_soap)
    except Exception:
        return ""

    if resp.status_code != 200:
        return ""

    try:
        root = ET.fromstring(resp.text)
    except Exception:
        return ""

    # Prefer alertingName, then asciiAlertingName, then description.
    alerting_elem = root.find('.//{*}alertingName')
    if alerting_elem is not None and alerting_elem.text and alerting_elem.text.strip():
        return alerting_elem.text.strip()

    ascii_alerting_elem = root.find('.//{*}asciiAlertingName')
    if ascii_alerting_elem is not None and ascii_alerting_elem.text and ascii_alerting_elem.text.strip():
        return ascii_alerting_elem.text.strip()

    desc_elem = root.find('.//{*}description')
    if desc_elem is not None and desc_elem.text and desc_elem.text.strip():
        return desc_elem.text.strip()

    return ""


print("==================================================")
print("  CUCM AXL - Extract ALL Line Groups (v1)")
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

# Optional filter for one line group, otherwise fetch all
line_group_filter = input("\nOptional Line Group name filter (press Enter for ALL): ").strip()
if line_group_filter:
    search_name = line_group_filter
else:
    search_name = "%"

# Phase 1: list line groups
list_soap = f"""<?xml version="1.0" encoding="UTF-8"?>
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

print("\nPhase 1: Querying CUCM for line groups...")
try:
    response = axl_post(session, CUCM_IP, list_soap)
except Exception as e:
    print(f"✗ Exception on listLineGroup: {e}")
    exit(1)

if response.status_code != 200:
    print(f"✗ listLineGroup failed - HTTP {response.status_code}")
    print(response.text)
    exit(1)

try:
    root = ET.fromstring(response.text)
except Exception as e:
    print(f"✗ XML parse error on listLineGroup response: {e}")
    exit(1)

line_group_names = []
for lg in root.findall('.//{*}lineGroup'):
    name_elem = lg.find('{*}name')
    if name_elem is not None and name_elem.text:
        line_group_names.append(name_elem.text.strip())

if not line_group_names:
    print("No line groups found for your search criteria.")
    exit(0)

print(f"Found {len(line_group_names)} line groups.")

# Prepare output
current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
output_dir = 'output_logs'
os.makedirs(output_dir, exist_ok=True)
log_filename = os.path.join(output_dir, f"extract_all_line_groups_{current_time}.csv")

rows = []
alerting_cache = {}

print("\nPhase 2: Fetching line members for each group...")
for idx, lg_name in enumerate(line_group_names, start=1):
    get_soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:getLineGroup>
         <name>{xml_escape(lg_name)}</name>
      </axl:getLineGroup>
   </soapenv:Body>
</soapenv:Envelope>"""

    try:
        detail_resp = axl_post(session, CUCM_IP, get_soap)
    except Exception as e:
        print(f"  ✗ [{idx}/{len(line_group_names)}] Exception for {lg_name}: {e}")
        rows.append([lg_name, "ERROR", str(e)])
        continue

    if detail_resp.status_code != 200:
        print(f"  ✗ [{idx}/{len(line_group_names)}] HTTP {detail_resp.status_code} for {lg_name}")
        rows.append([lg_name, "ERROR", f"HTTP {detail_resp.status_code}"])
        continue

    try:
        detail_root = ET.fromstring(detail_resp.text)
    except Exception as e:
        print(f"  ✗ [{idx}/{len(line_group_names)}] XML parse error for {lg_name}: {e}")
        rows.append([lg_name, "ERROR", "XML parse error"])
        continue

    # Parse members by finding directory number structures in getLineGroup response.
    members = []
    for dirn in detail_root.findall('.//{*}dirn'):
        pattern_elem = dirn.find('{*}pattern')
        partition_elem = dirn.find('{*}routePartitionName')
        pattern = pattern_elem.text.strip() if pattern_elem is not None and pattern_elem.text else "None"
        partition = partition_elem.text.strip() if partition_elem is not None and partition_elem.text else "None"
        if pattern != "None":
            members.append((pattern, partition))

    # Fallback for environments where getLineGroup returns directoryNumber instead of dirn.
    if not members:
        for dn in detail_root.findall('.//{*}directoryNumber'):
            pattern_elem = dn.find('{*}pattern')
            partition_elem = dn.find('{*}routePartitionName')
            pattern = pattern_elem.text.strip() if pattern_elem is not None and pattern_elem.text else "None"
            partition = partition_elem.text.strip() if partition_elem is not None and partition_elem.text else "None"
            if pattern != "None":
                members.append((pattern, partition))

    if members:
        for pattern, partition in members:
            rows.append([lg_name, pattern, partition, ""])
        print(f"  ✓ [{idx}/{len(line_group_names)}] {lg_name} - {len(members)} extension(s)")
    else:
        rows.append([lg_name, "", "", ""])
        print(f"  ✓ [{idx}/{len(line_group_names)}] {lg_name} - no extensions found")

unique_dns = sorted({(row[1], row[2]) for row in rows if row[1] and row[2]})
total_dns = len(unique_dns)

if total_dns > 0:
    print(f"\nPhase 3: Looking up Alerting Name for {total_dns} unique extension(s)...")
    for lookup_idx, (pattern, partition) in enumerate(unique_dns, start=1):
        cache_key = f"{pattern}|{partition}"
        if cache_key not in alerting_cache:
            alerting_cache[cache_key] = lookup_alerting_name(session, CUCM_IP, pattern, partition)
        print(f"  [{lookup_idx}/{total_dns}] Lookup {pattern} ({partition})")

    for row in rows:
        pattern = row[1]
        partition = row[2]
        if pattern and partition:
            row[3] = alerting_cache.get(f"{pattern}|{partition}", "")
else:
    print("\nPhase 3: No extensions found. Skipping Alerting Name lookup.")

with open(log_filename, 'w', newline='', encoding='utf-8') as logfile:
    log_writer = csv.writer(logfile)
    log_writer.writerow(['Line Group Name', 'Extension', 'Route Partition', 'Alerting Name'])
    log_writer.writerows(rows)

print("\n✓ Extraction complete!")
print(f"  Line groups processed : {len(line_group_names)}")
print(f"  Output file           : {log_filename}")
