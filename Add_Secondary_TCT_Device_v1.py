import csv
import datetime
import getpass
import json
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
DEFAULT_ROUTE_PARTITION = "ENT_DEVICE_PT"
TEMPLATE_FILE = "phone_device_template_tct.json"


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
            return {"name": "LAB", "cucm_ip": LAB_CUCM_IP}
        if choice in {"2", "PROD", "PRODUCTION"}:
            return {"name": "PRODUCTION", "cucm_ip": PROD_CUCM_IP}

        print("Invalid choice. Enter 0, 1, 2, LAB, or PROD.")


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
        "associatedDevices": associated_devices,
        "primaryExtension": {
            "pattern": primary_pattern,
            "routePartitionName": primary_partition,
        },
    }


def load_template(template_path):
    with open(template_path, "r", encoding="utf-8") as template_handle:
        return json.load(template_handle)


def phone_exists(session, cucm_ip, phone_name):
    soap = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:axl=\"http://www.cisco.com/AXL/API/15.0\">
   <soapenv:Body>
      <axl:getPhone>
         <name>{escape(phone_name)}</name>
      </axl:getPhone>
   </soapenv:Body>
</soapenv:Envelope>"""

    response = axl_post(session, cucm_ip, soap)
    return response.status_code == 200


def build_add_tct_phone_soap(user_details, new_phone_name, dn_pattern, dn_partition, template):
    optional_fields = []
    for tag_name, key_name in [
        ("callingSearchSpaceName", "callingSearchSpaceName"),
        ("devicePoolName", "devicePoolName"),
        ("commonPhoneConfigName", "commonPhoneConfigName"),
        ("locationName", "locationName"),
        ("mediaResourceListName", "mediaResourceListName"),
        ("presenceGroupName", "presenceGroupName"),
        ("subscribeCallingSearchSpaceName", "subscribeCallingSearchSpaceName"),
        ("rerouteCallingSearchSpaceName", "rerouteCallingSearchSpaceName"),
        ("sipProfileName", "sipProfileName"),
        ("softkeyTemplateName", "softkeyTemplateName"),
    ]:
        value = template.get(key_name, "").strip()
        if value:
            optional_fields.append(f"            <{tag_name}>{escape(value)}</{tag_name}>")

    optional_fields.append(
        f"            <securityProfileName>{escape(template['securityProfileName'])}</securityProfileName>"
    )
    optional_fields.append(
        f"            <phoneTemplateName>{escape(template['phoneTemplateName'])}</phoneTemplateName>"
    )

    display_name = " ".join(
        p for p in [user_details.get("firstName", ""), user_details.get("lastName", "")] if p
    ).strip() or user_details.get("displayName", "") or user_details["userid"]

    description = f"TCT {display_name}".strip()

    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:axl=\"http://www.cisco.com/AXL/API/15.0\">
   <soapenv:Body>
      <axl:addPhone>
         <phone>
            <name>{escape(new_phone_name)}</name>
            <description>{escape(description)}</description>
            <product>{escape(template['product'])}</product>
            <class>{escape(template['class'])}</class>
            <protocol>{escape(template['protocol'])}</protocol>
            <protocolSide>{escape(template['protocolSide'])}</protocolSide>
            {chr(10).join(optional_fields)}
            <ownerUserName>{escape(user_details['userid'])}</ownerUserName>
            <lines>
               <line>
                  <index>1</index>
                  <dirn>
                     <pattern>{escape(dn_pattern)}</pattern>
                     <routePartitionName>{escape(dn_partition)}</routePartitionName>
                  </dirn>
                  <label>{escape(dn_pattern)}</label>
                  <display>{escape(display_name)}</display>
                  <displayAscii>{escape(display_name)}</displayAscii>
                        <e164Mask>{escape(dn_pattern)}</e164Mask>
                        <callInfoDisplay>
                            <callerName>true</callerName>
                            <callerNumber>true</callerNumber>
                            <redirectedNumber>true</redirectedNumber>
                            <dialedNumber>true</dialedNumber>
                        </callInfoDisplay>
                                <maxNumCalls>{escape(template['lineMaxNumCalls'])}</maxNumCalls>
                                <busyTrigger>{escape(template['lineBusyTrigger'])}</busyTrigger>
               </line>
            </lines>
         </phone>
      </axl:addPhone>
   </soapenv:Body>
</soapenv:Envelope>"""


def build_update_user_devices_soap(userid, associated_devices):
    device_xml = "\n".join(
        f"            <device>{escape(device)}</device>" for device in associated_devices
    )

    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:axl=\"http://www.cisco.com/AXL/API/15.0\">
   <soapenv:Body>
      <axl:updateUser>
         <userid>{escape(userid)}</userid>
         <associatedDevices>
{device_xml}
         </associatedDevices>
      </axl:updateUser>
   </soapenv:Body>
</soapenv:Envelope>"""


def main():
    print("============================================================")
    print(" CUCM AXL - Add Secondary TCT Device (Shared Existing DN)")
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

    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), TEMPLATE_FILE)
    if not os.path.exists(template_path):
        print(f"Template file not found: {template_path}")
        return

    template = load_template(template_path)

    target_user = input("Enter userid to add secondary TCT for (e.g., Sarah.Paris): ").strip()
    if not target_user:
        print("No userid entered. Exiting.")
        return

    current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log_file = os.path.join(OUTPUT_DIR, f"add_secondary_tct_{target_user}_{current_time}.csv")

    with open(log_file, "w", newline="", encoding="utf-8") as log_handle:
        writer = csv.writer(log_handle)
        writer.writerow(["Step", "Status", "Details"])

        try:
            user_details = get_user_details(session, cucm_ip, target_user)
            writer.writerow(["Lookup User", "Success", f"Found {user_details['userid']}"])
            print(f"Found user: {user_details['userid']}")

            dn_pattern = (user_details.get("primaryExtension", {}).get("pattern") or "").strip()
            dn_partition = (user_details.get("primaryExtension", {}).get("routePartitionName") or "").strip() or DEFAULT_ROUTE_PARTITION

            if not dn_pattern:
                raise RuntimeError("End User does not have a primary extension. Set primary extension first, then rerun.")

            new_device = f"TCT{dn_pattern}"

            writer.writerow(["Resolve DN", "Success", f"Using End User primary extension {dn_pattern}/{dn_partition}"])
            writer.writerow(["Resolve Device Name", "Success", f"Target device name {new_device}"])
            print(f"Using End User primary extension: {dn_pattern}/{dn_partition}")
            print(f"New device name: {new_device}")

            if new_device in user_details.get("associatedDevices", []):
                writer.writerow(["Check Target Device", "Skipped", f"{new_device} already associated to user"])
                print(f"{new_device} is already associated to this user. No change needed.")
                print(f"Results logged to: {log_file}")
                return

            if phone_exists(session, cucm_ip, new_device):
                raise RuntimeError(f"Target device {new_device} already exists in CUCM. Choose a different name.")

            writer.writerow([
                "Template",
                "Success",
                (
                    f"product={template['product']}; class={template['class']}; protocol={template['protocol']}; "
                    f"securityProfile={template['securityProfileName']}; phoneTemplate={template['phoneTemplateName']}"
                ),
            ])

            add_phone_soap = build_add_tct_phone_soap(
                user_details,
                new_device,
                dn_pattern,
                dn_partition,
                template,
            )
            add_phone_resp = axl_post(session, cucm_ip, add_phone_soap)
            if add_phone_resp.status_code != 200:
                raise RuntimeError(f"Add TCT phone failed HTTP {add_phone_resp.status_code}: {add_phone_resp.text[:1200]}")

            writer.writerow(["Add TCT Device", "Success", f"Created {new_device} with shared DN {dn_pattern}"])
            print(f"Added device: {new_device}")

            updated_devices = list(user_details.get("associatedDevices", []))
            if new_device not in updated_devices:
                updated_devices.append(new_device)

            update_user_soap = build_update_user_devices_soap(user_details["userid"], updated_devices)
            update_user_resp = axl_post(session, cucm_ip, update_user_soap)
            if update_user_resp.status_code != 200:
                raise RuntimeError(f"Update user association failed HTTP {update_user_resp.status_code}: {update_user_resp.text[:1200]}")

            writer.writerow(["Update End User", "Success", f"Associated {new_device} to user {user_details['userid']}"])
            print(f"Updated user association for {user_details['userid']}")

            print("\nSecondary TCT build complete. No voicemail actions were performed.")
            print(f"Results logged to: {log_file}")

        except Exception as exc:
            writer.writerow(["Script", "Error", str(exc)])
            print(f"\nScript failed: {exc}")
            print(f"Results logged to: {log_file}")


if __name__ == "__main__":
    main()
