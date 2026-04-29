import urllib3
import requests
from requests.auth import HTTPBasicAuth
import csv
import getpass
import os
import datetime # <-- 1. Add this new import

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Ask for credentials before opening the session
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
vmprofile = 'VM_Profile_10Digits'

# <-- 2. Generate the dynamic filename
current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") # Formats as YYYYMMDD_HHMMSS
output_dir = 'output_logs'
os.makedirs(output_dir, exist_ok=True)
log_filename = os.path.join(output_dir, f"directory_add_{current_time}.csv")

# <-- 3. Open both the input CSV and the new log CSV
with open('patterns.csv', 'r', encoding='utf-8-sig') as infile, \
     open(log_filename, 'w', newline='', encoding='utf-8') as logfile:
    
    reader = csv.DictReader(infile)
    
    # Set up the CSV writer for the log file and write the header row
    log_writer = csv.writer(logfile)
    log_writer.writerow(['Pattern', 'Status', 'Details']) 
    
    for row in reader:
        pattern = row['pattern']
        
        add_soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:axl="http://www.cisco.com/AXL/API/15.0">
   <soapenv:Body>
      <axl:addLine>
         <line>
            <pattern>{pattern}</pattern>
            <routePartitionName>ENT_DEVICE_PT</routePartitionName>
            <description>CNAM:AMNHelathcare {pattern} Automation Use Only</description>
            <usage>Device</usage>
            <aarKeepCallHistory>true</aarKeepCallHistory>
            <aarVoiceMailEnabled>false</aarVoiceMailEnabled>
            <callForwardAll>
               <forwardToVoiceMail>false</forwardToVoiceMail>
               <callingSearchSpaceName>Cfwd_LD_CSS</callingSearchSpaceName>
            </callForwardAll>
            <callForwardBusy>
               <forwardToVoiceMail>true</forwardToVoiceMail>
            </callForwardBusy>
            <callForwardBusyInt>
               <forwardToVoiceMail>true</forwardToVoiceMail>
            </callForwardBusyInt>
            <callForwardNoAnswer>
               <forwardToVoiceMail>true</forwardToVoiceMail>
            </callForwardNoAnswer>
            <callForwardNoAnswerInt>
               <forwardToVoiceMail>true</forwardToVoiceMail>
            </callForwardNoAnswerInt>
            <callForwardNoCoverage>
               <forwardToVoiceMail>true</forwardToVoiceMail>
            </callForwardNoCoverage>
            <callForwardNoCoverageInt>
               <forwardToVoiceMail>true</forwardToVoiceMail>
            </callForwardNoCoverageInt>
            <callForwardOnFailure>
               <forwardToVoiceMail>true</forwardToVoiceMail>
            </callForwardOnFailure>
            <callForwardNotRegistered>
               <forwardToVoiceMail>true</forwardToVoiceMail>
            </callForwardNotRegistered>
            <callForwardNotRegisteredInt>
               <forwardToVoiceMail>true</forwardToVoiceMail>
            </callForwardNotRegisteredInt>
            <autoAnswer>Auto Answer Off</autoAnswer>
            <callingIdPresentationWhenDiverted>Default</callingIdPresentationWhenDiverted>
            <presenceGroupName>Standard Presence group</presenceGroupName>
            <shareLineAppearanceCssName>COR_Intl_CSS</shareLineAppearanceCssName>
            <voiceMailProfileName>{vmprofile}</voiceMailProfileName>
            <patternPrecedence>Default</patternPrecedence>
            <cfaCssPolicy>Use System Default</cfaCssPolicy>
            <partyEntranceTone>Default</partyEntranceTone>
            <allowCtiControlFlag>true</allowCtiControlFlag>
            <rejectAnonymousCall>false</rejectAnonymousCall>
            <patternUrgency>false</patternUrgency>
            <useEnterpriseAltNum>false</useEnterpriseAltNum>
            <useE164AltNum>false</useE164AltNum>
            <active>false</active>
         </line>
      </axl:addLine>
   </soapenv:Body>
</soapenv:Envelope>"""
        
        url = f'https://{CUCM_IP}:8443/axl/'
        
        try:
            response = session.post(url, data=add_soap, headers={'Content-Type': 'text/xml'})
            if response.status_code == 200:
                print(f"✓ Added: {pattern}")
                # <-- Write success to log
                log_writer.writerow([pattern, 'Success', 'Added successfully']) 
            else:
                print(f"✗ Failed: {pattern} - Status {response.status_code}")
                # <-- Write failure to log
                log_writer.writerow([pattern, 'Failed', f'HTTP Status {response.status_code}']) 
        except Exception as e:
            print(f"✗ Error: {pattern} - {e}")
            # <-- Write exception error to log
            log_writer.writerow([pattern, 'Error', str(e)])

# Let the user know the script finished and where to find the logs
print(f"\nScript complete! Results logged to: {log_filename}")