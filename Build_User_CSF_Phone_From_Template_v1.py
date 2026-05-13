import csv
import datetime
import getpass
import json
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


def _ad_format_phone_dashes(phone):
    """Format 10-digit number as 999-999-9999 for telephoneNumber."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"
    return phone


def _ad_format_phone_plain(phone):
    """Return digits-only 10-digit number for ipPhone."""
    digits = re.sub(r"\D", "", phone)
    return digits if len(digits) == 10 else phone


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


def update_ad_phone_fields(samaccountname, phone_number):
    """
    Set telephoneNumber (dashes) and ipPhone (digits) for the given sAMAccountName.
    Returns dict with keys: success, telephone, ipphone, message.
    """
    if not PYAD_AVAILABLE:
        return {"success": False, "message": "pyad not installed (pip install pyad)"}
    try:
        user = _find_ad_user(samaccountname)
        if not user:
            return {"success": False, "message": f"AD user '{samaccountname}' not found"}
        phone_dashes = _ad_format_phone_dashes(phone_number)
        phone_plain  = _ad_format_phone_plain(phone_number)
        user.update_attribute("telephoneNumber", phone_dashes)
        user.update_attribute("ipPhone", phone_plain)
        return {"success": True, "telephone": phone_dashes, "ipphone": phone_plain, "message": "Updated"}
    except Exception as e:
        return {"success": False, "message": str(e)}


LAB_CUCM_IP = "lascucmpl01.ahs.int"
PROD_CUCM_IP = "lascucmpp01.ahs.int"
TEMPLATE_FILE = "phone_device_template_lab_csf.json"
OUTPUT_DIR = "output_logs"
ROUTE_PARTITION = "ENT_DEVICE_PT"
UNITY_LAB_SERVER = "LASCUTYPL01.ahs.int"
UNITY_PROD_SERVER = "SANCUTYP01.ahs.int"
DEFAULT_VM_PIN = "56219"
LDAP_INTEGRATION_ENABLED = True
UNITY_ENV_SETTINGS = {
    "LAB": {
        "server": UNITY_LAB_SERVER,
        "template_alias": "T3-CST",
        "default_pin": DEFAULT_VM_PIN,
        "ldap_import_enabled": True,
    },
    "PRODUCTION": {
        "server": UNITY_PROD_SERVER,
        "template_alias": "T3-CST",
        "default_pin": DEFAULT_VM_PIN,
        "ldap_import_enabled": True,
    },
}


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


def sanitize_for_filename(value):
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return safe.strip("._") or "unknown_user"


def choose_environment():
    while True:
        print("\nSelect CUCM Environment:")
        print("  1 - PRODUCTION (lascucmpp01.ahs.int)")
        print("  2 - LAB        (lascucmpl01.ahs.int)")
        print("  0 - Return to main menu")
        choice = input("Enter choice (0, 1, or 2): ").strip()
        if choice == "0":
            return None, None
        if choice == "1":
            return PROD_CUCM_IP, "PRODUCTION"
        if choice == "2":
            return LAB_CUCM_IP, "LAB"
        print("Invalid choice. Please enter 0, 1, or 2.")


def choose_dn_prefix():
    while True:
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
        if choice == "2":
            return "214", "General FTE"
        if choice == "3":
            return "945", "Strike"
        print("Invalid choice. Please enter 0, 1, 2, or 3.")


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


def extract_object_id_from_location(response):
    location = response.headers.get("Location", "").strip()
    if not location:
        return None
    clean = location.rstrip("/")
    if "/vmrest/users/" not in clean:
        return None
    return clean.split("/")[-1] or None


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


def get_unity_user_by_object_id(session, unity_server, object_id):
    url = make_unity_url(unity_server, f"/vmrest/users/{object_id}")
    response = session.get(url, headers=unity_headers(), timeout=120)
    if response.status_code != 200:
        raise RuntimeError(f"Unity user detail lookup failed: {parse_unity_error_text(response)}")

    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def user_is_ldap_integrated(user, expected_ldap_user_id=None):
    if not isinstance(user, dict):
        return False

    integrated_flag = user.get("IsLdapIntegrated")
    if isinstance(integrated_flag, bool) and integrated_flag:
        return True
    if isinstance(integrated_flag, str) and integrated_flag.strip().lower() in {"true", "1", "yes"}:
        return True

    ldap_type = str(user.get("LdapType") or "").strip()
    ldap_pkid = str(user.get("LdapCcmPkid") or "").strip()
    ldap_user_id = str(user.get("LdapCcmUserId") or "").strip()
    if ldap_type == "3" and ldap_pkid:
        if not expected_ldap_user_id:
            return True
        return ldap_user_id.lower() == str(expected_ldap_user_id).strip().lower()

    return False


def get_import_user_by_alias(session, unity_server, alias):
    endpoint = make_unity_url(unity_server, "/vmrest/import/users/ldap")
    queries = [
        f"(alias is {alias})",
        f"(alias is {alias.lower()})",
        f"(alias startswith {alias.split('.')[0]})",
    ]

    for query in queries:
        response = session.get(endpoint, headers=unity_headers(), params={"query": query}, timeout=120)
        if response.status_code == 404:
            raise RuntimeError("Unity import endpoint /vmrest/import/users/ldap is not available.")
        if response.status_code in {401, 403}:
            raise RuntimeError("Access denied for Unity import endpoint with current credentials.")
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
            if str(import_user.get("alias", "")).strip().lower() == alias.lower():
                return import_user

        if len(import_users) == 1:
            return import_users[0]

    return None


def import_ldap_user_with_new_vm(session, unity_server, import_pkid, extension, template_alias):
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
        raise RuntimeError(f"Unity LDAP import failed: {parse_unity_error_text(response)}")

    object_id = extract_object_id_from_location(response)
    if object_id:
        return object_id

    body_text = (response.text or "").strip()
    if "/vmrest/users/" in body_text:
        return body_text.rstrip("/").split("/")[-1]

    return None


def create_local_unity_user_with_mailbox(
    session, unity_server, alias, first_name, last_name, display_name, extension, email_address, template_alias
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
        "TemplateAlias": template_alias,
    }

    response = session.post(url, headers=unity_headers(), json=payload, timeout=120)
    if response.status_code not in {200, 201}:
        retry = session.post(
            url,
            headers=unity_headers(),
            params={"templateAlias": template_alias},
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
        if retry.status_code not in {200, 201}:
            raise RuntimeError(f"Unity local user create failed: {parse_unity_error_text(retry)}")
        response = retry

    if response.text:
        try:
            data = response.json()
            return data.get("ObjectId") or extract_object_id_from_location(response)
        except ValueError:
            return extract_object_id_from_location(response)

    return extract_object_id_from_location(response)


def update_existing_unity_user_mailbox(session, unity_server, object_id, extension, email_address):
    url = make_unity_url(unity_server, f"/vmrest/users/{object_id}")
    payload = {
        "DtmfAccessId": extension,
        "EmailAddress": email_address,
    }
    response = session.put(url, headers=unity_headers(), json=payload, timeout=120)
    if response.status_code not in {200, 204}:
        raise RuntimeError(f"Unity mailbox update failed: {parse_unity_error_text(response)}")


def set_unity_pin(session, unity_server, object_id, pin):
    url = make_unity_url(unity_server, f"/vmrest/users/{object_id}/credential/pin")
    payload = {
        "Credentials": pin,
        "CredMustChange": "true",
    }
    response = session.put(url, headers=unity_headers(), json=payload, timeout=120)
    if response.status_code not in {200, 201, 204}:
        raise RuntimeError(f"Unity PIN update failed: {parse_unity_error_text(response)}")


def create_or_update_unity_voicemail(
    session,
    unity_server,
    alias,
    first_name,
    last_name,
    display_name,
    extension,
    email_address,
    template_alias,
    ldap_import_enabled,
):
    existing_user = get_unity_user_by_alias(session, unity_server, alias)
    if existing_user:
        object_id = existing_user.get("ObjectId")
        if not object_id:
            raise RuntimeError("Unity user exists but ObjectId is missing.")
        update_existing_unity_user_mailbox(session, unity_server, object_id, extension, email_address)
        return object_id, "updated"

    if ldap_import_enabled:
        import_user = get_import_user_by_alias(session, unity_server, alias)
        if import_user and import_user.get("pkid"):
            object_id = import_ldap_user_with_new_vm(
                session,
                unity_server,
                import_user.get("pkid"),
                extension,
                template_alias,
            )
            if object_id:
                return object_id, "imported"

    object_id = create_local_unity_user_with_mailbox(
        session,
        unity_server,
        alias,
        first_name,
        last_name,
        display_name,
        extension,
        email_address,
        template_alias,
    )
    return object_id, "created_local"


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

    unity_config = UNITY_ENV_SETTINGS[env_name]
    unity_server = unity_config["server"]
    unity_template_alias = unity_config["template_alias"]
    unity_default_pin = unity_config["default_pin"]
    unity_ldap_import_enabled = unity_config["ldap_import_enabled"]
    print(f"Using {env_name} Unity Connection: {unity_server}")

    unity_session = requests.Session()
    unity_session.verify = False
    unity_session.auth = HTTPBasicAuth(cucm_user, cucm_pass)

    session = requests.Session()
    session.verify = False
    session.auth = HTTPBasicAuth(cucm_user, cucm_pass)

    while True:
        dn_prefix, dn_type_name = choose_dn_prefix()
        if dn_prefix is None:
            return
        print(f"Using DN type: {dn_type_name} ({dn_prefix}xxxxxxx)")

        target_user = input("Username of the person who needs Cisco Jabber (or 0 to return): ").strip()

        if target_user == "0":
            return

        if not target_user:
            print("No target userid provided. Returning to main menu.")
            return

        print("\nBuild request summary:")
        print(f"  Environment: {env_name} ({cucm_ip})")
        print(f"  DN Type: {dn_type_name} ({dn_prefix})")
        print(f"  Target User: {target_user}")
        if not confirm_yes_no("Proceed with this build request?", default_yes=True):
            print("Request canceled. Returning to selection.")
            continue

        current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        safe_target_user = sanitize_for_filename(target_user)
        log_filename = os.path.join(OUTPUT_DIR, f"build_user_csf_phone_{safe_target_user}_{current_time}.csv")

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
                log_writer.writerow([
                    "Unity Settings",
                    "Info",
                    (
                        f"server={unity_server}; templateAlias={unity_template_alias}; "
                        f"ldapImportEnabled={unity_ldap_import_enabled}; defaultPin={unity_default_pin}"
                    ),
                ])
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

                            unity_email = user_details.get("mailid", "").strip() or f"{user_details['userid'].lower()}@amnhealthcare.com"
                            unity_first = user_details.get("firstName", "").strip() or user_details["userid"]
                            unity_last = user_details.get("lastName", "").strip() or user_details["userid"]
                            unity_display = display_name

                            try:
                                unity_object_id, unity_action = create_or_update_unity_voicemail(
                                    unity_session,
                                    unity_server,
                                    user_details["userid"],
                                    unity_first,
                                    unity_last,
                                    unity_display,
                                    new_dn,
                                    unity_email,
                                    unity_template_alias,
                                    unity_ldap_import_enabled,
                                )
                                log_writer.writerow([
                                    "Unity Voicemail",
                                    "Success",
                                    f"{unity_action} Unity mailbox for {user_details['userid']} on {unity_server} using extension {new_dn}",
                                ])
                                print(f"✓ Unity voicemail {unity_action} for {user_details['userid']}")

                                latest_unity_user = get_unity_user_by_object_id(unity_session, unity_server, unity_object_id)
                                if latest_unity_user and not user_is_ldap_integrated(latest_unity_user, user_details["userid"]):
                                    print(
                                        "\nAction required: In Unity Connection, open this user and check 'Integrate with LDAP Directory', then save."
                                    )
                                    input("Press Enter after saving LDAP integration in Unity: ")
                                    log_writer.writerow([
                                        "Unity LDAP Integration",
                                        "Manual",
                                        "Admin confirmed manual LDAP integration checkbox",
                                    ])

                                set_unity_pin(unity_session, unity_server, unity_object_id, unity_default_pin)
                                log_writer.writerow([
                                    "Unity PIN",
                                    "Success",
                                    f"Set default PIN ({unity_default_pin}) and forced change for {user_details['userid']}",
                                ])
                                print("✓ Unity PIN set successfully")
                            except Exception as unity_error:
                                log_writer.writerow(["Unity Voicemail", "Failed", str(unity_error)])
                                print(f"✗ Unity voicemail step failed: {unity_error}")

                        # ── Update AD phone fields ──────────────────────────
                        try:
                            ad_result = update_ad_phone_fields(user_details["userid"], new_dn)
                            if ad_result["success"]:
                                log_writer.writerow([
                                    "AD Update",
                                    "Success",
                                    f"telephoneNumber={ad_result['telephone']}, ipPhone={ad_result['ipphone']}",
                                ])
                                print(f"✓ AD fields updated: telephoneNumber={ad_result['telephone']}, ipPhone={ad_result['ipphone']}")
                            else:
                                log_writer.writerow(["AD Update", "Warning", ad_result["message"]])
                                print(f"⚠ AD update skipped: {ad_result['message']}")
                        except Exception as ad_err:
                            log_writer.writerow(["AD Update", "Error", str(ad_err)])
                            print(f"⚠ AD update error: {ad_err}")

                log_writer.writerow(["Script", "Error", err_msg])
                if "The specified User was not found" in err_msg or "5007" in err_msg:
                    print(f"\n✗ Invalid User: '{target_user}' was not found in CUCM.")
                    print(f"Results logged to: {log_filename}")
                    continue
                print(f"✗ Script error: {e}")
                print(f"Results logged to: {log_filename}")

        print("\nWhat would you like to do next?")
        if run_success:
            print("  1 - Run another Build CSF (reuse cached login)")
        else:
            print("  1 - Try another Build CSF (reuse cached login)")
        print("  0 - Return to main menu")
        next_choice = input("Enter choice (0 or 1): ").strip()
        if next_choice != "1":
            return


if __name__ == "__main__":
    main()
