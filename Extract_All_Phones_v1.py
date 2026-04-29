import urllib3
import requests
from requests.auth import HTTPBasicAuth
import csv
import getpass
import os
import datetime
import xml.etree.ElementTree as ET

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# Generate the dynamic filename
current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
output_dir = 'output_logs'
os.makedirs(output_dir, exist_ok=True)
log_filename = os.path.join(output_dir, f"extract_all_phones_{current_time}.csv")

with open(log_filename, 'w', newline='', encoding='utf-8') as logfile:

    log_writer = csv.writer(logfile)
    log_writer.writerow([
        'Device Name',
        'Description',
        'Model',
        'Protocol',
        'Device Pool',
        'Location',
        'Calling Search Space',
        'Owner User ID',
        'Common Device Config'
    ])

    list_soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:listPhone>
         <searchCriteria>
            <name>%</name>
         </searchCriteria>
         <returnedTags>
            <name/>
            <description/>
            <model/>
            <protocol/>
            <devicePoolName/>
            <locationName/>
            <callingSearchSpaceName/>
            <ownerUserName/>
            <commonDeviceConfigName/>
         </returnedTags>
      </axl:listPhone>
   </soapenv:Body>
</soapenv:Envelope>"""

    url = f'https://{CUCM_IP}:8443/axl/'

    print("Querying CUCM for all phone devices... This may take a moment depending on system size.")

    try:
        response = session.post(url, data=list_soap, headers={'Content-Type': 'text/xml'})

        if response.status_code == 200:
            root = ET.fromstring(response.text)

            phones = root.findall('.//{*}phone')
            print(f"Found {len(phones)} phone devices! Writing to CSV...")

            for phone in phones:
                name_elem    = phone.find('{*}name')
                desc_elem    = phone.find('{*}description')
                model_elem   = phone.find('{*}model')
                proto_elem   = phone.find('{*}protocol')
                pool_elem    = phone.find('{*}devicePoolName')
                loc_elem     = phone.find('{*}locationName')
                css_elem     = phone.find('{*}callingSearchSpaceName')
                owner_elem   = phone.find('{*}ownerUserName')
                cdc_elem     = phone.find('{*}commonDeviceConfigName')

                name        = name_elem.text    if name_elem    is not None else "None"
                description = desc_elem.text    if desc_elem    is not None else "None"
                model       = model_elem.text   if model_elem   is not None else "None"
                protocol    = proto_elem.text   if proto_elem   is not None else "None"
                device_pool = pool_elem.text    if pool_elem    is not None else "None"
                location    = loc_elem.text     if loc_elem     is not None else "None"
                css         = css_elem.text     if css_elem     is not None else "None"
                owner       = owner_elem.text   if owner_elem   is not None else "None"
                cdc         = cdc_elem.text     if cdc_elem     is not None else "None"

                log_writer.writerow([name, description, model, protocol, device_pool, location, css, owner, cdc])

            print(f"✓ Extraction complete! Results saved to: {log_filename}")

        else:
            print(f"✗ Error Connecting - HTTP Status {response.status_code}")
            print(response.text)

    except Exception as e:
        print(f"✗ Script Exception: {e}")
