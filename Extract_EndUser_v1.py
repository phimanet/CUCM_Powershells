import urllib3
import requests
from requests.auth import HTTPBasicAuth
import csv
import getpass
import datetime
import xml.etree.ElementTree as ET

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -------------------------
# Helpers
# -------------------------
def axl_post(session, cucm_host, soap_xml):
    url = f"https://{cucm_host}:8443/axl/"
    headers = {"Content-Type": "text/xml"}
    return session.post(url, data=soap_xml.encode("utf-8"), headers=headers, timeout=60)

def strip_ns(tag):
    """Remove XML namespace prefix from a tag."""
    return tag.split("}", 1)[-1] if "}" in tag else tag

def find_child(elem, tag_name):
    """Find first direct child by local tag name (namespace-agnostic)."""
    for child in list(elem):
        if strip_ns(child.tag) == tag_name:
            return child
    return None

def find_first_text(elem, path_candidates):
    """
    Try multiple tag paths (namespace-agnostic).
    Each candidate is a list of tag names to traverse.
    Returns first non-empty text found.
    """
    for path in path_candidates:
        cur = elem
        ok = True
        for t in path:
            nxt = find_child(cur, t)
            if nxt is None:
                ok = False
                break
            cur = nxt
        if ok and cur is not None and cur.text is not None:
            txt = cur.text.strip()
            if txt != "":
                return txt
    return ""

def safe_int_choice(prompt, min_val, max_val):
    while True:
        val = input(prompt).strip()
        if val.isdigit():
            i = int(val)
            if min_val <= i <= max_val:
                return i
        print(f"Please enter a number between {min_val} and {max_val}.")

def flatten_xml(elem, prefix=""):
    """
    Recursively walk an XML element and flatten ALL fields into a list of (key, value) tuples.
    - Scalar elements:  key = dotted path, value = text
    - List elements:    key = dotted path [1], [2], etc.
    - Attributes:       key = dotted path @attr_name
    """
    results = []
    tag = strip_ns(elem.tag)
    current_key = f"{prefix}.{tag}" if prefix else tag

    # Capture any attributes on this element
    for attr_key, attr_val in elem.attrib.items():
        clean_attr = strip_ns(attr_key)
        # Skip xmlns-type attributes
        if clean_attr.startswith("xmlns") or "schemas.xmlsoap" in attr_val or "cisco.com/AXL" in attr_val:
            continue
        if attr_val.strip():
            results.append((f"{current_key}@{clean_attr}", attr_val.strip()))

    children = list(elem)

    if not children:
        # Leaf node — capture text
        if elem.text and elem.text.strip():
            results.append((current_key, elem.text.strip()))
    else:
        # Has children — check for repeated tags (lists)
        child_tag_count = {}
        for child in children:
            ctag = strip_ns(child.tag)
            child_tag_count[ctag] = child_tag_count.get(ctag, 0) + 1

        tag_index = {}
        for child in children:
            ctag = strip_ns(child.tag)
            if child_tag_count[ctag] > 1:
                # Repeated tag = list item, number them
                tag_index[ctag] = tag_index.get(ctag, 0) + 1
                child_prefix = f"{current_key}.{ctag}[{tag_index[ctag]}]"
                # Recurse into child but skip re-adding the tag name
                for sub_child in list(child):
                    results.extend(flatten_xml(sub_child, prefix=child_prefix))
                # Also capture direct text of the list item itself
                if child.text and child.text.strip():
                    results.append((child_prefix, child.text.strip()))
                # Capture attributes on the list item
                for ak, av in child.attrib.items():
                    ca = strip_ns(ak)
                    if not ca.startswith("xmlns") and av.strip():
                        results.append((f"{child_prefix}@{ca}", av.strip()))
            else:
                # Single child — recurse normally
                results.extend(flatten_xml(child, prefix=current_key))

    return results

# -------------------------
# Main
# -------------------------
def main():
    print("==================================================")
    print(" CUCM AXL - Export End User by Last Name (v1)")
    print("==================================================\n")

    # Credentials (same pattern as framework scripts)
    cucm_user = input("Enter CUCM Username: ").strip()
    cucm_pass = getpass.getpass("Enter CUCM Password: ")

    session = requests.Session()
    session.verify = False
    session.auth = HTTPBasicAuth(cucm_user, cucm_pass)

    CUCM_HOST = "lascucmpl01.ahs.int"

    last_name = input("\nEnter Last Name to search: ").strip()
    if not last_name:
        print("No last name provided. Exiting.")
        return

    # -------------------------
    # Step 1: listUser (search by last name)
    # -------------------------
    list_user_soap = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
  <soapenv:Header/>
  <soapenv:Body>
    <axl:listUser sequence="?">
      <searchCriteria>
        <lastName>{last_name}%</lastName>
      </searchCriteria>
      <returnedTags>
        <userid/>
        <firstName/>
        <lastName/>
        <displayName/>
        <mailid/>
        <department/>
      </returnedTags>
    </axl:listUser>
  </soapenv:Body>
</soapenv:Envelope>"""

    try:
        resp = axl_post(session, CUCM_HOST, list_user_soap)
    except Exception as e:
        print(f"\n✗ Exception calling AXL listUser: {e}")
        return

    if resp.status_code != 200:
        print(f"\n✗ AXL listUser failed. HTTP {resp.status_code}")
        print(resp.text[:2000])
        return

    try:
        root = ET.fromstring(resp.text)
    except Exception as e:
        print(f"\n✗ Could not parse listUser XML: {e}")
        return

    user_nodes = [el for el in root.iter() if strip_ns(el.tag) == "user"]

    if not user_nodes:
        print(f"\nNo users found matching last name starting with: {last_name}")
        return

    matches = []
    for u in user_nodes:
        matches.append({
            "userid":      find_first_text(u, [["userid"]]),
            "firstName":   find_first_text(u, [["firstName"]]),
            "lastName":    find_first_text(u, [["lastName"]]),
            "displayName": find_first_text(u, [["displayName"]]),
            "mailid":      find_first_text(u, [["mailid"]]),
            "department":  find_first_text(u, [["department"]]),
        })

    # -------------------------
    # Step 2: Select user if multiple matches
    # -------------------------
    if len(matches) > 1:
        print(f"\nFound {len(matches)} matches:")
        print("--------------------------------------------------")
        for idx, r in enumerate(matches, start=1):
            print(f"{idx:>2}. {r['lastName']}, {r['firstName']} | userid={r['userid']} | display={r['displayName']} | mail={r['mailid']} | dept={r['department']}")
        print("--------------------------------------------------")
        choice = safe_int_choice("Select a user to export (number): ", 1, len(matches))
        selected = matches[choice - 1]
    else:
        selected = matches[0]
        print(f"\nOne match found: {selected['lastName']}, {selected['firstName']} (userid={selected['userid']})")

    userid = selected["userid"]
    if not userid:
        print("\n✗ Selected match has no userid. Cannot proceed.")
        return

    # -------------------------
    # Step 3: getUser — NO returnedTags = return EVERYTHING
    # -------------------------
    get_user_soap = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
  <soapenv:Header/>
  <soapenv:Body>
    <axl:getUser sequence="?">
      <userid>{userid}</userid>
    </axl:getUser>
  </soapenv:Body>
</soapenv:Envelope>"""

    print(f"\nFetching all available fields for userid: {userid} ...")

    try:
        resp2 = axl_post(session, CUCM_HOST, get_user_soap)
    except Exception as e:
        print(f"\n✗ Exception calling AXL getUser: {e}")
        return

    if resp2.status_code != 200:
        print(f"\n✗ AXL getUser failed. HTTP {resp2.status_code}")
        print(resp2.text[:2000])
        return

    try:
        root2 = ET.fromstring(resp2.text)
    except Exception as e:
        print(f"\n✗ Could not parse getUser XML: {e}")
        return

    # Locate the <user> node inside getUserResponse > return > user
    user_node = None
    for el in root2.iter():
        if strip_ns(el.tag) == "user":
            user_node = el
            break

    if user_node is None:
        print("\n✗ Could not locate <user> in getUser response.")
        print(resp2.text[:2000])
        return

    # -------------------------
    # Step 4: Dynamically flatten the ENTIRE <user> XML tree
    # -------------------------
    flat_data = flatten_xml(user_node)

    if not flat_data:
        print("\n✗ No fields extracted from user node.")
        return

    # -------------------------
    # Step 5: Write to CSV (Field, Value)
    # -------------------------
    current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = f"enduser_export_{userid}_{current_time}.csv"

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Field", "Value"])
        for field_name, field_value in flat_data:
            writer.writerow([field_name, field_value])

    # Print summary to console
    print(f"\n✓ Export complete!")
    print(f"  Total fields extracted : {len(flat_data)}")
    print(f"  Output file            : {out_file}")
    print(f"\n  Preview (first 30 fields):")
    print("  --------------------------------------------------")
    for field_name, field_value in flat_data[:30]:
        display_val = field_value if len(field_value) <= 60 else field_value[:57] + "..."
        print(f"  {field_name:<50} {display_val}")
    if len(flat_data) > 30:
        print(f"  ... and {len(flat_data) - 30} more fields in the CSV.")
    print("  --------------------------------------------------")

if __name__ == "__main__":
    main()