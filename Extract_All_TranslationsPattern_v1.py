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
log_filename = f"extract_translation_patterns_{current_time}.csv"

with open(log_filename, 'w', newline='', encoding='utf-8') as logfile:
    
    log_writer = csv.writer(logfile)
    # <-- 1. Added the new transformation masks to the CSV headers
    log_writer.writerow([
        'Translation Pattern', 
        'Route Partition', 
        'Description', 
        'Calling Search Space', 
        'Calling Party Transform Mask', 
        'Called Party Transform Mask'
    ]) 
    
    # <-- 2. Added the requested tags to the SOAP envelope
    list_soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:listTransPattern>
         <searchCriteria>
            <pattern>%</pattern>
         </searchCriteria>
         <returnedTags>
            <pattern/>
            <routePartitionName/>
            <description/>
            <callingSearchSpaceName/>
            <callingPartyTransformationMask/>
            <calledPartyTransformationMask/>
         </returnedTags>
      </axl:listTransPattern>
   </soapenv:Body>
</soapenv:Envelope>"""
    
    url = f'https://{CUCM_IP}:8443/axl/'
    
    print("Querying CUCM for all Translation Patterns... This may take a moment.")
    
    try:
        response = session.post(url, data=list_soap, headers={'Content-Type': 'text/xml'})
        
        if response.status_code == 200:
            root = ET.fromstring(response.text)
            
            patterns = root.findall('.//{*}transPattern')
            print(f"Found {len(patterns)} translation patterns! Writing to CSV...")
            
            for tp in patterns:
                # <-- 3. Find the XML elements
                pat_element = tp.find('{*}pattern')
                part_element = tp.find('{*}routePartitionName')
                desc_element = tp.find('{*}description')
                css_element = tp.find('{*}callingSearchSpaceName')
                call_mask_elem = tp.find('{*}callingPartyTransformationMask')
                called_mask_elem = tp.find('{*}calledPartyTransformationMask')
                
                # <-- 4. Safely extract the text
                pattern = pat_element.text if pat_element is not None else "None"
                partition = part_element.text if part_element is not None else "None"
                description = desc_element.text if desc_element is not None else "None"
                css = css_element.text if css_element is not None else "None"
                calling_mask = call_mask_elem.text if call_mask_elem is not None else "None"
                called_mask = called_mask_elem.text if called_mask_elem is not None else "None"
                
                # <-- 5. Write everything to the new row
                log_writer.writerow([
                    pattern, 
                    partition, 
                    description, 
                    css, 
                    calling_mask, 
                    called_mask
                ]) 
                
            print(f"✓ Extraction complete! Results saved to: {log_filename}")
            
        else:
            print(f"✗ Error Connecting - HTTP Status {response.status_code}")
            print(response.text) 
            
    except Exception as e:
        print(f"✗ Script Exception: {e}")