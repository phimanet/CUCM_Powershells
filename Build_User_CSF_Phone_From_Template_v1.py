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
TEMPLATE_FILE = "phone_device_template_lab_csf.json"
OUTPUT_DIR = "output_logs"
ROUTE_PARTITION = "ENT_DEVICE_PT"


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


def load_template(template_path):
    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)


def confirm_yes_no(prompt, default_yes=True):
    suffix = " [Y/n]: " if default_yes else " [y/N]: "
    value = input(prompt + suffix).strip().lower()
    if not value:
        return default_yes
    return value in ["y", "yes"]


def choose_environment():
    print("\nSelect CUCM Environment:")
    print("  1 - PRODUCTION (lascucmpp01.ahs.int)")
    print("  2 - LAB        (lascucmpl01.ahs.int)")
    print("  0 - Return to main menu")
    choice = input("Enter choice (0, 1, or 2): ").strip()
    if choice == "0":
        return None, None
    if choice == "1":
        return PROD_CUCM_IP, "PRODUCTION"
    return LAB_CUCM_IP, "LAB"


def choose_dn_prefix():
    print("\nSelect Directory Number Type:")
    print("  1 - Recruiter (469)")
    print("  2 - General FTE (214)")
    print("  3 - Strike (945)")
    print("  0 - Return to main menu")
    choice = input("Enter choice (0, 1, 2, or 3): ").strip()
    if choice == "0":
        return None, None
    if choice == "1":
        return "469", "Recruiter"
    if choice == "3":
        return "945", "Strike"
    return "214", "General FTE"


def get_user_details(session, cucm_ip, username):
    soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
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

    return {
        "userid": find_first_text(user_node, [["userid"]]),
        "firstName": find_first_text(user_node, [["firstName"]]),
        "lastName": find_first_text(user_node, [["lastName"]]),
        "displayName": find_first_text(user_node, [["displayName"]]),
        "mailid": find_first_text(user_node, [["mailid"]]),
        "associatedDevices": associated_devices,
    }


def list_available_dns(session, cucm_ip, prefix):
    soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:listLine>
         <searchCriteria>
            <pattern>{escape(prefix)}%</pattern>
         </searchCriteria>
         <returnedTags>
            <pattern/>
            <routePartitionName/>
            <active/>
         </returnedTags>
      </axl:listLine>
   </soapenv:Body>
</soapenv:Envelope>"""

    response = axl_post(session, cucm_ip, soap)
    if response.status_code != 200:
        raise RuntimeError(f"listLine for prefix {prefix} failed with HTTP {response.status_code}: {response.text[:1000]}")

    root = ET.fromstring(response.text)
    candidates = []
    for elem in root.iter():
        if strip_ns(elem.tag) != "line":
            continue

        pattern = find_first_text(elem, [["pattern"]])
        partition = find_first_text(elem, [["routePartitionName"]])
        active = find_first_text(elem, [["active"]]).strip().lower()

        is_inactive = active not in {"true", "t", "1", "yes"}
        if pattern and partition == ROUTE_PARTITION and is_inactive:
            candidates.append(pattern)

    return sorted(set(candidates))


def is_dn_unassigned(session, cucm_ip, pattern, route_partition):
    soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:getLine>
         <pattern>{escape(pattern)}</pattern>
         <routePartitionName>{escape(route_partition)}</routePartitionName>
         <returnedTags>
            <pattern/>
            <routePartitionName/>
            <associatedDevices>
               <device/>
            </associatedDevices>
         </returnedTags>
      </axl:getLine>
   </soapenv:Body>
</soapenv:Envelope>"""

    response = axl_post(session, cucm_ip, soap)
    if response.status_code != 200:
        return False

    try:
        root = ET.fromstring(response.text)
    except Exception:
        return False

    for elem in root.iter():
        if strip_ns(elem.tag) == "device" and elem.text and elem.text.strip():
            return False

    return True


def choose_available_dn(session, cucm_ip, prefix):
    candidates = list_available_dns(session, cucm_ip, prefix)
    for candidate in candidates:
        if is_dn_unassigned(session, cucm_ip, candidate, ROUTE_PARTITION):
            return candidate
    raise RuntimeError(f"No available inactive DN found in {ROUTE_PARTITION} starting with {prefix}.")


def build_add_phone_soap(template, user_details, phone_name, description, new_dn, display_name):
    optional_fields = []
    for tag_name, key_name in [
        ("callingSearchSpaceName", "callingSearchSpaceName"),
        ("devicePoolName", "devicePoolName"),
        ("commonPhoneConfigName", "commonPhoneConfigName"),
        ("locationName", "locationName"),
        ("mediaResourceListName", "mediaResourceListName"),
        ("securityProfileName", "securityProfileName"),
        ("sipProfileName", "sipProfileName"),
        ("phoneTemplateName", "phoneTemplateName"),
        ("presenceGroupName", "presenceGroupName"),
        ("subscribeCallingSearchSpaceName", "subscribeCallingSearchSpaceName"),
        ("rerouteCallingSearchSpaceName", "rerouteCallingSearchSpaceName"),
        ("userLocale", "userLocale"),
        ("networkLocale", "networkLocale"),
    ]:
        value = template.get(key_name, "").strip()
        if value:
            optional_fields.append(f"            <{tag_name}>{escape(value)}</{tag_name}>")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:addPhone>
         <phone>
            <name>{escape(phone_name)}</name>
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
                     <pattern>{escape(new_dn)}</pattern>
                     <routePartitionName>{escape(template['routePartitionName'])}</routePartitionName>
                  </dirn>
                  <label>{escape(new_dn)}</label>
                  <display>{escape(display_name)}</display>
                  <displayAscii>{escape(display_name)}</displayAscii>
                  <alertingName>{escape(display_name)}</alertingName>
                  <asciiAlertingName>{escape(display_name)}</asciiAlertingName>
                  <e164Mask>{escape(new_dn)}</e164Mask>
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


def build_update_line_soap(new_dn, route_partition, display_name):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:updateLine>
         <pattern>{escape(new_dn)}</pattern>
         <routePartitionName>{escape(route_partition)}</routePartitionName>
         <alertingName>{escape(display_name)}</alertingName>
         <asciiAlertingName>{escape(display_name)}</asciiAlertingName>
      </axl:updateLine>
   </soapenv:Body>
</soapenv:Envelope>"""


def build_update_user_soap(user_details, phone_name, new_dn, route_partition, line_description):
    associated_devices = list(user_details["associatedDevices"])
    if phone_name not in associated_devices:
        associated_devices.append(phone_name)

    device_xml = "\n".join(
        f"            <device>{escape(device)}</device>" for device in associated_devices
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:updateUser>
         <userid>{escape(user_details['userid'])}</userid>
         <associatedDevices>
{device_xml}
         </associatedDevices>
         <associatedGroups>
            <userGroup>
               <name>AMN User</name>
            </userGroup>
         </associatedGroups>
         <primaryExtension>
            <pattern>{escape(new_dn)}</pattern>
            <routePartitionName>{escape(route_partition)}</routePartitionName>
         </primaryExtension>
         <lineAppearanceAssociationForPresences>
            <lineAppearanceAssociationForPresence>
               <laapAssociate>t</laapAssociate>
               <laapProductType>Cisco Unified Client Services Framework</laapProductType>
               <laapDeviceName>{escape(phone_name)}</laapDeviceName>
               <laapDirectory>{escape(new_dn)}</laapDirectory>
               <laapPartition>{escape(route_partition)}</laapPartition>
               <laapDescription>{escape(line_description)}</laapDescription>
            </lineAppearanceAssociationForPresence>
         </lineAppearanceAssociationForPresences>
         <homeCluster>true</homeCluster>
         <serviceProfile>Service_Profile_IM</serviceProfile>
      </axl:updateUser>
   </soapenv:Body>
</soapenv:Envelope>"""


def main():
    print("==================================================")
    print(" CUCM AXL - Build CSF Phone From Static Template")
    print("==================================================\n")

    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), TEMPLATE_FILE)
    if not os.path.exists(template_path):
        print(f"Template file not found: {template_path}")
        return

    template = load_template(template_path)

    cucm_ip, env_name = choose_environment()
    if cucm_ip is None:
        return
    print(f"Using {env_name} CUCM: {cucm_ip}")

    cucm_user = input("Enter CUCM Username: ").strip()
    cucm_pass = getpass.getpass("Enter CUCM Password: ")

    session = requests.Session()
    session.verify = False
    session.auth = HTTPBasicAuth(cucm_user, cucm_pass)

    while True:
        dn_prefix, dn_type_name = choose_dn_prefix()
        if dn_prefix is None:
            return
        print(f"Using DN type: {dn_type_name} ({dn_prefix}xxxxxxx)")

        target_user = input("Enter target End User userid (example: Sarah.Paris): ").strip()

        if not target_user:
            print("No target userid provided. Returning to main menu.")
            return

        current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        log_filename = os.path.join(OUTPUT_DIR, f"build_user_csf_phone_{target_user}_{current_time}.csv")

        run_success = False

        with open(log_filename, "w", newline="", encoding="utf-8") as logfile:
            log_writer = csv.writer(logfile)
            log_writer.writerow(["Step", "Status", "Details"])

            try:
                user_details = get_user_details(session, cucm_ip, target_user)
                full_name = " ".join(part for part in [user_details["firstName"], user_details["lastName"]] if part).strip()
                display_name = full_name or user_details["displayName"] or user_details["userid"]
                new_dn = choose_available_dn(session, cucm_ip, dn_prefix)
                phone_name = f"{template['deviceNamePrefix']}{new_dn}"
                description = f"CSF {display_name}".strip()

                print(f"Found user: {user_details['userid']} | {display_name}")
                print(f"Selected DN: {new_dn}")
                print(f"New phone name: {phone_name}")

                log_writer.writerow(["Environment", "Success", f"{env_name} ({cucm_ip})"])
                log_writer.writerow(["DN Type", "Success", f"{dn_type_name} ({dn_prefix})"])
                log_writer.writerow(["Lookup User", "Success", f"Found user {user_details['userid']} ({display_name})"])
                log_writer.writerow(["Select DN", "Success", f"Using available DN {new_dn}"])

                add_phone_soap = build_add_phone_soap(template, user_details, phone_name, description, new_dn, display_name)
                update_line_soap = build_update_line_soap(new_dn, template["routePartitionName"], display_name)
                update_user_soap = build_update_user_soap(user_details, phone_name, new_dn, template["routePartitionName"], description)

                add_response = axl_post(session, cucm_ip, add_phone_soap)
                if add_response.status_code != 200:
                    log_writer.writerow(["Add Phone", "Failed", f"HTTP {add_response.status_code}: {add_response.text[:1000]}"])
                    print(f"✗ Add Phone failed. HTTP {add_response.status_code}")
                    print(add_response.text[:2000])
                else:
                    log_writer.writerow(["Add Phone", "Success", f"Created {phone_name} with DN {new_dn}"])
                    print(f"✓ Added phone {phone_name}")

                    line_response = axl_post(session, cucm_ip, update_line_soap)
                    if line_response.status_code != 200:
                        log_writer.writerow(["Update Line", "Failed", f"HTTP {line_response.status_code}: {line_response.text[:1000]}"])
                        print(f"✗ Update Line failed. HTTP {line_response.status_code}")
                        print(line_response.text[:2000])
                    else:
                        log_writer.writerow(["Update Line", "Success", f"Updated alerting names on DN {new_dn}"])
                        print(f"✓ Updated line alerting fields for {new_dn}")

                        update_response = axl_post(session, cucm_ip, update_user_soap)
                        if update_response.status_code != 200:
                            log_writer.writerow(["Update User", "Failed", f"HTTP {update_response.status_code}: {update_response.text[:1000]}"])
                            print(f"✗ Update User failed. HTTP {update_response.status_code}")
                            print(update_response.text[:2000])
                        else:
                            log_writer.writerow(["Update User", "Success", f"Updated {user_details['userid']} with phone {phone_name} and DN {new_dn}"])
                            print(f"✓ Updated end user {user_details['userid']}")
                            print(f"\nScript complete! Results logged to: {log_filename}")
                            run_success = True

            except Exception as e:
                err_msg = str(e)
                log_writer.writerow(["Script", "Error", err_msg])
                if "The specified User was not found" in err_msg or "5007" in err_msg:
                    print(f"\n✗ Invalid User: '{target_user}' was not found in CUCM.")
                    print(f"Results logged to: {log_filename}")
                    continue
                print(f"✗ Script error: {e}")
                print(f"Results logged to: {log_filename}")

        if not run_success:
            return

        print("\nWhat would you like to do next?")
        print("  1 - Run another Build CSF")
        print("  0 - Return to main menu")
        next_choice = input("Enter choice (0 or 1): ").strip()
        if next_choice != "1":
            return


if __name__ == "__main__":
    main()
