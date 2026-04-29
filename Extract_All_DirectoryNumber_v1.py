import urllib3
import requests
from requests.auth import HTTPBasicAuth
import csv
import getpass
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
log_filename = f"extract_all_lines_{current_time}.csv"

# <-- 1. We no longer need to open 'patterns.csv'. Just open the new log file.
with open(log_filename, 'w', newline='', encoding='utf-8') as logfile:
    
    log_writer = csv.writer(logfile)
    log_writer.writerow(['Pattern', 'Description', 'Usage', 'Route Partition', 'Voicemail Profile', 'Active']) 
    
    # <-- 2. Change to listLine with a wildcard (%) and request our specific tags back
    list_soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:listLine>
         <searchCriteria>
            <pattern>%</pattern>
         </searchCriteria>
         <returnedTags>
            <pattern/>
            <description/>
            <usage/>
            <routePartitionName/>
            <voiceMailProfileName/>
            <active/>
         </returnedTags>
      </axl:listLine>
   </soapenv:Body>
</soapenv:Envelope>"""
    
    url = f'https://{CUCM_IP}:8443/axl/'
    
    print("Querying CUCM for all lines... This may take a moment depending on system size.")
    
    try:
        response = session.post(url, data=list_soap, headers={'Content-Type': 'text/xml'})
        
        if response.status_code == 200:
            root = ET.fromstring(response.text)
            
            # <-- 3. Find ALL instances of <line> in the massive XML response
            lines = root.findall('.//{*}line')
            print(f"Found {len(lines)} lines! Writing to CSV...")
            
            # <-- 4. Loop through the returned XML instead of looping through a CSV
            for line in lines:
                pat_element = line.find('{*}pattern')
                desc_element = line.find('{*}description')
                usage_element = line.find('{*}usage')
                part_element = line.find('{*}routePartitionName')
                vmp_element = line.find('{*}voiceMailProfileName')
                active_element = line.find('{*}active')
                
                # Safely extract text
                pattern = pat_element.text if pat_element is not None else "None"
                description = desc_element.text if desc_element is not None else "None"
                usage = usage_element.text if usage_element is not None else "None"
                partition = part_element.text if part_element is not None else "None"
                vm_profile = vmp_element.text if vmp_element is not None else "None"
                active = active_element.text if active_element is not None else "None"
                
                # Write this specific line to the CSV
                log_writer.writerow([pattern, description, usage, partition, vm_profile, active]) 
                
            print(f"✓ Extraction complete! Results saved to: {log_filename}")
            
        else:
            print(f"✗ Error Connecting - HTTP Status {response.status_code}")
            print(response.text) # Prints the exact XML error from CUCM
            
    except Exception as e:
        print(f"✗ Script Exception: {e}")