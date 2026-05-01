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


def axl_post(session, cucm_ip, soap_xml):
    url = f"https://{cucm_ip}:8443/axl/"
    headers = {"Content-Type": "text/xml"}
    return session.post(url, data=soap_xml.encode("utf-8"), headers=headers, timeout=120)


def strip_ns(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def flatten_xml(elem, prefix=""):
    results = []
    tag = strip_ns(elem.tag)
    current_key = f"{prefix}.{tag}" if prefix else tag

    for attr_key, attr_val in elem.attrib.items():
        clean_attr = strip_ns(attr_key)
        if clean_attr.startswith("xmlns") or "schemas.xmlsoap" in str(attr_val) or "cisco.com/AXL" in str(attr_val):
            continue
        if str(attr_val).strip():
            results.append((f"{current_key}@{clean_attr}", str(attr_val).strip()))

    children = list(elem)
    if not children:
        if elem.text and elem.text.strip():
            results.append((current_key, elem.text.strip()))
        return results

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
                if not ca.startswith("xmlns") and str(av).strip():
                    results.append((f"{child_prefix}@{ca}", str(av).strip()))
        else:
            results.extend(flatten_xml(child, prefix=current_key))

    return results


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
            return {"name": "LAB", "cucm_ip": LAB_CUCM_IP}
        if choice in {"2", "PROD", "PRODUCTION"}:
            return {"name": "PRODUCTION", "cucm_ip": PROD_CUCM_IP}
        print("Invalid choice. Enter 0, 1, 2, LAB, or PROD.")


def main():
    print("============================================================")
    print(" CUCM AXL - Export Device All Properties")
    print("============================================================")

    env = choose_environment()
    if env is None:
        return

    cucm_ip = env["cucm_ip"]
    print(f"Using {env['name']} CUCM: {cucm_ip}")

    cucm_user = input("Enter CUCM Username: ").strip()
    cucm_pass = getpass.getpass("Enter CUCM Password: ")

    session = requests.Session()
    session.verify = False
    session.auth = HTTPBasicAuth(cucm_user, cucm_pass)

    device_name = input("Enter device name (e.g., CSF8584652166, TCT8584652166, BOT8584652166): ").strip().upper()
    if not device_name:
        print("No device name entered. Exiting.")
        return

    soap = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:axl=\"http://www.cisco.com/AXL/API/15.0\">
   <soapenv:Body>
      <axl:getPhone>
            <name>{escape(device_name)}</name>
      </axl:getPhone>
   </soapenv:Body>
</soapenv:Envelope>"""

    current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    csv_file = os.path.join(OUTPUT_DIR, f"device_export_{device_name}_{current_time}.csv")
    xml_file = os.path.join(OUTPUT_DIR, f"device_export_{device_name}_{current_time}.xml")

    try:
        response = axl_post(session, cucm_ip, soap)
    except Exception as exc:
        print(f"Request failed: {exc}")
        return

    if response.status_code != 200:
        print(f"getPhone failed for {device_name} with HTTP {response.status_code}")
        print(response.text[:2000])
        return

    with open(xml_file, "w", encoding="utf-8") as xml_handle:
        xml_handle.write(response.text)

    try:
        root = ET.fromstring(response.text)
    except Exception as exc:
        print(f"Could not parse XML response: {exc}")
        print(f"Raw XML saved to: {xml_file}")
        return

    phone_node = None
    for elem in root.iter():
        if strip_ns(elem.tag) == "phone":
            phone_node = elem
            break

    if phone_node is None:
        print("Could not locate phone node in getPhone response.")
        print(f"Raw XML saved to: {xml_file}")
        return

    flat_data = flatten_xml(phone_node)

    with open(csv_file, "w", newline="", encoding="utf-8") as csv_handle:
        writer = csv.writer(csv_handle)
        writer.writerow(["Field", "Value"])
        for field_name, field_value in flat_data:
            writer.writerow([field_name, field_value])

    print(f"\nExport complete for {device_name}.")
    print(f"Fields exported: {len(flat_data)}")
    print(f"CSV file: {csv_file}")
    print(f"XML file: {xml_file}")


if __name__ == "__main__":
    main()
