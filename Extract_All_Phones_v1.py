import urllib3
import requests
from requests.auth import HTTPBasicAuth
import csv
import getpass
import os
import datetime
import xml.etree.ElementTree as ET

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -------------------------
# Helpers (same pattern as Extract_EndUser_v1.py)
# -------------------------
def axl_post(session, cucm_ip, soap_xml):
    url = f"https://{cucm_ip}:8443/axl/"
    headers = {"Content-Type": "text/xml"}
    return session.post(url, data=soap_xml.encode("utf-8"), headers=headers, timeout=120)

def strip_ns(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag

def flatten_xml(elem, prefix=""):
    """Recursively flatten an XML element into (key, value) tuples."""
    results = []
    tag = strip_ns(elem.tag)
    current_key = f"{prefix}.{tag}" if prefix else tag

    for attr_key, attr_val in elem.attrib.items():
        clean_attr = strip_ns(attr_key)
        if clean_attr.startswith("xmlns") or "schemas.xmlsoap" in attr_val or "cisco.com/AXL" in attr_val:
            continue
        if attr_val.strip():
            results.append((f"{current_key}@{clean_attr}", attr_val.strip()))

    children = list(elem)
    if not children:
        if elem.text and elem.text.strip():
            results.append((current_key, elem.text.strip()))
    else:
        child_tag_count = {}
        for child in children:
            ctag = strip_ns(child.tag)
            child_tag_count[ctag] = child_tag_count.get(ctag, 0) + 1

        tag_index = {}
        for child in children:
            ctag = strip_ns(child.tag)
            if child_tag_count[ctag] > 1:
                tag_index[ctag] = tag_index.get(ctag, 0) + 1
                child_prefix = f"{current_key}.{ctag}[{tag_index[ctag]}]"
                for sub_child in list(child):
                    results.extend(flatten_xml(sub_child, prefix=child_prefix))
                if child.text and child.text.strip():
                    results.append((child_prefix, child.text.strip()))
                for ak, av in child.attrib.items():
                    ca = strip_ns(ak)
                    if not ca.startswith("xmlns") and av.strip():
                        results.append((f"{child_prefix}@{ca}", av.strip()))
            else:
                results.extend(flatten_xml(child, prefix=current_key))
    return results

# -------------------------
# Main
# -------------------------
print("==================================================")
print("  CUCM AXL - Extract ALL Phone Devices (v1)")
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

# -------------------------
# Phase 1: listPhone — get all device names
# -------------------------
list_soap = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:listPhone>
         <searchCriteria>
            <name>%</name>
         </searchCriteria>
         <returnedTags>
            <name/>
         </returnedTags>
      </axl:listPhone>
   </soapenv:Body>
</soapenv:Envelope>"""

print("\nPhase 1: Querying CUCM for all phone device names...")
try:
    resp = axl_post(session, CUCM_IP, list_soap)
except Exception as e:
    print(f"✗ Exception on listPhone: {e}")
    exit(1)

if resp.status_code != 200:
    print(f"✗ listPhone failed - HTTP {resp.status_code}")
    print(resp.text[:2000])
    exit(1)

root = ET.fromstring(resp.text)
phone_nodes = [el for el in root.iter() if strip_ns(el.tag) == "phone"]
device_names = []
for p in phone_nodes:
    for child in p:
        if strip_ns(child.tag) == "name" and child.text:
            device_names.append(child.text.strip())

print(f"Found {len(device_names)} phone devices.")

if not device_names:
    print("No devices found. Exiting.")
    exit(0)

# -------------------------
# Phase 2: getPhone per device — pull ALL fields
# -------------------------
print(f"\nPhase 2: Fetching full details for each device (this may take a while)...")

all_rows = []       # list of dicts: {field: value}
all_headers = []    # ordered unique list of all field names seen
headers_seen = set()

for idx, name in enumerate(device_names, start=1):
    get_soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:getPhone>
         <name>{name}</name>
      </axl:getPhone>
   </soapenv:Body>
</soapenv:Envelope>"""

    try:
        resp2 = axl_post(session, CUCM_IP, get_soap)
    except Exception as e:
        print(f"  ✗ [{idx}/{len(device_names)}] Exception for {name}: {e}")
        all_rows.append({"name": name, "_error": str(e)})
        continue

    if resp2.status_code != 200:
        print(f"  ✗ [{idx}/{len(device_names)}] HTTP {resp2.status_code} for {name}")
        all_rows.append({"name": name, "_error": f"HTTP {resp2.status_code}"})
        continue

    try:
        root2 = ET.fromstring(resp2.text)
    except Exception as e:
        print(f"  ✗ [{idx}/{len(device_names)}] XML parse error for {name}: {e}")
        all_rows.append({"name": name, "_error": "XML parse error"})
        continue

    phone_node = None
    for el in root2.iter():
        if strip_ns(el.tag) == "phone":
            phone_node = el
            break

    if phone_node is None:
        print(f"  ✗ [{idx}/{len(device_names)}] No <phone> node in response for {name}")
        all_rows.append({"name": name, "_error": "No phone node in response"})
        continue

    flat = flatten_xml(phone_node)
    row_dict = dict(flat)

    for key in row_dict:
        if key not in headers_seen:
            headers_seen.add(key)
            all_headers.append(key)

    all_rows.append(row_dict)
    print(f"  ✓ [{idx}/{len(device_names)}] {name} — {len(flat)} fields")

# -------------------------
# Write CSV — dynamic headers covering all devices
# -------------------------
current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
output_dir = 'output_logs'
os.makedirs(output_dir, exist_ok=True)
log_filename = os.path.join(output_dir, f"extract_all_phones_{current_time}.csv")

with open(log_filename, 'w', newline='', encoding='utf-8') as logfile:
    log_writer = csv.DictWriter(logfile, fieldnames=all_headers, extrasaction='ignore')
    log_writer.writeheader()
    for row in all_rows:
        log_writer.writerow(row)

print(f"\n✓ Export complete!")
print(f"  Total devices exported : {len(all_rows)}")
print(f"  Total fields captured  : {len(all_headers)}")
print(f"  Output file            : {log_filename}")

            print(f"✓ Extraction complete! Results saved to: {log_filename}")

        else:
            print(f"✗ Error Connecting - HTTP Status {response.status_code}")
            print(response.text)

    except Exception as e:
        print(f"✗ Script Exception: {e}")
