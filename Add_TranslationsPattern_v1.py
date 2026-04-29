import urllib3
import requests
from requests.auth import HTTPBasicAuth
import csv
import getpass
import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Ask for credentials securely
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
input_csv = 'translation_patterns_insert_template.csv'

# Generate the dynamic filename for the log
current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
log_filename = f"translation_add_log_{current_time}.csv"

with open(input_csv, 'r', encoding='utf-8-sig') as infile, \
     open(log_filename, 'w', newline='', encoding='utf-8') as logfile:
    
    reader = csv.DictReader(infile)
    
    log_writer = csv.writer(logfile)
    # Set headers for the output log
    log_writer.writerow([
        'Translation Pattern', 
        'Route Partition', 
        'Status', 
        'Details'
    ]) 
    
    for row in reader:
        # Extract variables using the exact headers from your CSV
        pattern = row.get('Translation Pattern', '').strip()
        partition = row.get('Route Partition', '').strip()
        description = row.get('Description', '').strip()
        css = row.get('Calling Search Space', '').strip()
        calling_mask = row.get('Calling Party Transform Mask', '').strip()
        called_mask = row.get('Called Party Transform Mask', '').strip()
        
        # Conditionally build optional XML tags only if they have data in the CSV
        call_mask_xml = f"<callingPartyTransformationMask>{calling_mask}</callingPartyTransformationMask>" if calling_mask else ""
        called_mask_xml = f"<calledPartyTransformationMask>{called_mask}</calledPartyTransformationMask>" if called_mask else ""
        css_xml = f"<callingSearchSpaceName>{css}</callingSearchSpaceName>" if css else ""
        
        # The corrected SOAP envelope with the <usage> tag included
        add_soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:addTransPattern>
         <transPattern>
            <pattern>{pattern}</pattern>
            <routePartitionName>{partition}</routePartitionName>
            <description>{description}</description>
            <usage>Translation</usage> 
            {css_xml}
            {call_mask_xml}
            {called_mask_xml}
         </transPattern>
      </axl:addTransPattern>
   </soapenv:Body>
</soapenv:Envelope>"""
        
        url = f'https://{CUCM_IP}:8443/axl/'
        
        try:
            response = session.post(url, data=add_soap, headers={'Content-Type': 'text/xml'})
            
            if response.status_code == 200:
                print(f"✓ Added: {pattern} in {partition}")
                log_writer.writerow([pattern, partition, 'Success', 'Added successfully']) 
            else:
                print(f"✗ Failed: {pattern} - Status {response.status_code}")
                # Parse the exact error from CUCM if possible, otherwise just log the status code
                log_writer.writerow([pattern, partition, 'Failed', f'HTTP Status {response.status_code} - Check CLI for XML details']) 
                # Print the raw text to the console so you can see exactly why it failed (e.g. duplicate pattern)
                print(response.text) 
                
        except Exception as e:
            print(f"✗ Error: {pattern} - {e}")
            log_writer.writerow([pattern, partition, 'Error', str(e)])

print(f"\nScript complete! Results logged to: {log_filename}")