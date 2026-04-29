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

CUCM_IP = 'lascucmpl01.ahs.int'

# Generate the dynamic filename
current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
log_filename = f"directory_extract_{current_time}.csv"

with open('patterns.csv', 'r', encoding='utf-8-sig') as infile, \
     open(log_filename, 'w', newline='', encoding='utf-8') as logfile:
    
    reader = csv.DictReader(infile)
    
    log_writer = csv.writer(logfile)
    # <-- 1. Update headers to include the new fields
    log_writer.writerow(['Pattern', 'Status', 'Description', 'Usage', 'Route Partition', 'Voicemail Profile', 'Active', 'Details']) 
    
    for row in reader:
        pattern = row['pattern']
        
        get_soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:getLine>
         <pattern>{pattern}</pattern>
         <routePartitionName>ENT_DEVICE_PT</routePartitionName>
      </axl:getLine>
   </soapenv:Body>
</soapenv:Envelope>"""
        
        url = f'https://{CUCM_IP}:8443/axl/'
        
        try:
            response = session.post(url, data=get_soap, headers={'Content-Type': 'text/xml'})
            
            if response.status_code == 200:
                root = ET.fromstring(response.text)
                
                # <-- 2. Locate the XML elements (the {*} ignores XML namespaces)
                desc_element = root.find('.//{*}description')
                usage_element = root.find('.//{*}usage')
                part_element = root.find('.//{*}routePartitionName')
                vmp_element = root.find('.//{*}voiceMailProfileName')
                active_element = root.find('.//{*}active')
                
                # <-- 3. Safely extract the text. If the tag doesn't exist or is empty, return "None"
                description = desc_element.text if desc_element is not None else "None"
                usage = usage_element.text if usage_element is not None else "None"
                partition = part_element.text if part_element is not None else "None"
                vm_profile = vmp_element.text if vmp_element is not None else "None"
                active = active_element.text if active_element is not None else "None"
                
                print(f"✓ Extracted: {pattern} | Desc: {description}")
                # <-- 4. Write all variables to the log
                log_writer.writerow([pattern, 'Found', description, usage, partition, vm_profile, active, 'Success']) 
                
            elif response.status_code == 404 or "Item not valid" in response.text:
                print(f"✗ Not Found: {pattern}")
                # Keep empty strings for the columns so the CSV stays aligned
                log_writer.writerow([pattern, 'Not Found', '', '', '', '', '', 'Line does not exist in this partition'])
                
            else:
                print(f"✗ Error: {pattern} - Status {response.status_code}")
                log_writer.writerow([pattern, 'Error', '', '', '', '', '', f'HTTP Status {response.status_code}']) 
                
        except Exception as e:
            print(f"✗ Exception: {pattern} - {e}")
            log_writer.writerow([pattern, 'Exception', '', '', '', '', '', str(e)])

print(f"\nExtraction complete! Results saved to: {log_filename}")