import urllib3
import requests
from requests.auth import HTTPBasicAuth
import getpass
import xml.etree.ElementTree as ET

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Ask for credentials only
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
    ldap_agreement_name = 'LDAP_AMN'
    print("Using PRODUCTION CUCM")
else:
    CUCM_IP = 'lascucmpl01.ahs.int'
    ldap_agreement_name = 'LAB_LDAP_AMN'
    print("Using LAB CUCM")

# Construct the SOAP request for doLdapSync
sync_soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <ns:doLdapSync>
         <name>{ldap_agreement_name}</name>
         <sync>true</sync>
      </ns:doLdapSync>
   </soapenv:Body>
</soapenv:Envelope>"""

url = f'https://{CUCM_IP}:8443/axl/'

try:
    print(f"\nInitiating LDAP sync for '{ldap_agreement_name}'...")
    response = session.post(url, data=sync_soap, headers={'Content-Type': 'text/xml'})
    
    if response.status_code == 200:
        root = ET.fromstring(response.text)
        
        # Locate the return element to confirm success
        return_element = root.find('.//{*}return')
        
        if return_element is not None:
            print(f"✓ Success: LDAP sync for '{ldap_agreement_name}' has been triggered.")
        else:
            print(f"✓ Success: Request accepted, but couldn't parse the exact return tag.")
            
    elif response.status_code == 500:
        # CUCM usually returns 500 for AXL faults (like an invalid name)
        root = ET.fromstring(response.text)
        fault_element = root.find('.//{*}faultstring')
        fault_msg = fault_element.text if fault_element is not None else "Unknown AXL Fault"
        print(f"✗ Error: {fault_msg}")
        
    elif response.status_code == 404:
        print(f"✗ Not Found: AXL service might be disabled or URL is incorrect.")
        
    else:
        print(f"✗ Error: HTTP Status {response.status_code}")
        print(f"  Raw response: {response.text}")
        
except Exception as e:
    print(f"✗ Exception: {e}")

print("\nScript execution complete!")