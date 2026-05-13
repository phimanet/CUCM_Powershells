# Security Audit Report
**Cisco CUCM/Unity Python Scripts Workspace**  
**Date:** May 5, 2026  
**Scope:** All 20 Python files in repository

---

## Executive Summary
**Risk Level: HIGH**

This workspace contains **multiple high-severity security issues** related to disabled SSL/TLS verification, credential handling, and XML processing. While no hardcoded credentials were found in source code, the pattern of SSL verification bypass affects all files that communicate over HTTPS to CUCM and Unity servers, creating vulnerability to man-in-the-middle (MITM) attacks.

---

## Critical Findings

### 1. **CRITICAL: Disabled SSL/TLS Certificate Verification**

**Severity:** CRITICAL (CVSS 7.4+)  
**Affected Files:** 17 files
- Add_DirectoryNumber_v1.py
- Add_Secondary_BOT_Device_v1.py
- Add_Secondary_TCT_Device_v1.py
- Add_Secondary_STRIKE_Devices_v1.py
- Add_TranslationsPattern_v1.py
- Build_User_CSF_Phone_From_Template_v1.py
- Compare_Unity_User_Before_After_LDAP_v1.py
- Create_Unity_Voicemail_Box_v1.py
- CUCM_LDAP_Sync_v1.py
- Decommission_User_CSF_Voicemail_v1.py
- Extract_All_DirectoryNumber_v1.py
- Extract_All_Phones_v1.py
- Extract_All_TranslationsPattern_v1.py
- Extract_DirectoryNumber_v1.py
- Extract_EndUser_v1.py
- Extract_TCT_Phone_Properties_v1.py
- Reset_Unity_Voicemail_Pin_v1.py

**Pattern:**
```python
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
...
session.verify = False
```

**Risk:**
- Disables certificate validation for HTTPS requests to CUCM/Unity servers
- Suppresses security warnings from urllib3
- Allows MITM attacks to intercept credentials and API calls
- Credentials sent in basic auth can be captured by attacker-in-the-middle

**Impact:** An attacker on the network can:
1. Intercept unencrypted credentials (HTTP Basic Auth)
2. Modify API requests/responses (e.g., add unauthorized users, delete devices)
3. Inject malicious SOAP/XML payloads
4. Exfiltrate configuration data

**Recommendation:**
```python
# CORRECT: Use proper certificate verification
session.verify = True  # Default - enable verification
# OR for self-signed certs in dev-only:
session.verify = '/path/to/custom/ca-bundle.pem'  # Use custom CA bundle
```

---

## High-Severity Findings

### 2. **HIGH: Unsafe XML Processing (XXE Vulnerability Potential)**

**Severity:** HIGH  
**Affected Files:** 10 files
- Add_Secondary_BOT_Device_v1.py (line 88)
- Add_Secondary_TCT_Device_v1.py (line 88)
- Add_Secondary_STRIKE_Devices_v1.py (line 89)
- Build_User_CSF_Phone_From_Template_v1.py (lines 490, 538, 578)
- CUCM_LDAP_Sync_v1.py (lines 49, 61)
- Decommission_User_CSF_Voicemail_v1.py (lines 179, 227, 293)
- Extract_All_DirectoryNumber_v1.py (line 72)
- Extract_All_Phones_v1.py (lines 120, 166)
- Extract_All_TranslationsPattern_v1.py (line 79)
- Extract_DirectoryNumber_v1.py (line 70)
- Extract_TCT_Phone_Properties_v1.py (line 141)
- Extract_EndUser_v1.py (lines 184, 252)

**Pattern:**
```python
root = ET.fromstring(response.text)
```

**Risk:**
- `ElementTree.fromstring()` is vulnerable to XXE (XML External Entity) attacks by default
- No defusedxml protection in place
- Untrusted XML from CUCM/Unity servers parsed without validation

**Impact:**
- XML bomb attacks (billion laughs attack)
- External entity expansion attacks
- Potential data exfiltration from server filesystem
- Denial of service via recursive entity expansion

**Recommendation:**
```python
import defusedxml.ElementTree as ET

# Use defusedxml instead of standard ElementTree
root = ET.fromstring(response.text)
```

---

### 3. **HIGH: Subprocess Command Injection Risk**

**Severity:** HIGH (Medium in this case due to limited control)  
**Affected Files:** 2 files
- Update_AD_Phone_Fields_v1.py (line 65-70)
- CUCM_Menu.py (line 23)

**Pattern:**
```python
# Update_AD_Phone_Fields_v1.py
completed = subprocess.run(
    ["powershell", "-NoProfile", "-Command", script],
    input=json.dumps(payload),
    capture_output=True,
    text=True,
    check=False,
)
```

**Risk:** 
- While using list form (good), credentials are passed via stdin as JSON
- PowerShell script is embedded in Python code
- CUCM_Menu.py uses `sys.executable` safely, but relies on PATH lookup

**Recommendation:**
```python
# Already correct pattern for subprocess.run - uses list, not shell=True
# Just ensure credentials are handled securely (done in Update_AD_Phone_Fields_v1.py)
```

---

### 4. **HIGH: Credentials in Function Parameters**

**Severity:** HIGH  
**Affected Files:** Update_AD_Phone_Fields_v1.py

**Current Status:** MITIGATED

Update_AD_Phone_Fields_v1.py has been hardened to store credentials only in local `auth_context` dictionary that is:
- Not global
- Scrubbed in finally block
- Passed only to necessary functions

**Recommendation:** MAINTAIN current implementation.

---

## Medium-Severity Findings

### 5. **MEDIUM: Unvalidated User Input**

**Severity:** MEDIUM  
**Affected Files:** All interactive scripts

**Pattern:**
```python
cucm_user = input("Enter CUCM Username: ")
phone_raw = input("Enter 10-digit phone number (e.g., 4695551234): ").strip()
target_user = input("Enter userid to add secondary BOT for: ").strip()
```

**Risk:**
- Minimal validation on user input
- Could contain special characters, escape sequences, or LDAP injection
- Phone number validation exists in some files, but not comprehensive

**Recommendation:**
```python
import re

def validate_phone_number(phone_str):
    """Validate 10-digit phone number."""
    digits = re.sub(r"\D", "", phone_str)
    if len(digits) != 10:
        raise ValueError(f"Invalid phone: must be exactly 10 digits, got {len(digits)}")
    return digits

def validate_username(username):
    """Validate username format."""
    if not re.match(r'^[a-zA-Z0-9._-]+$', username):
        raise ValueError("Invalid username: contains forbidden characters")
    if len(username) > 64:
        raise ValueError("Invalid username: too long")
    return username
```

---

### 6. **MEDIUM: Insufficient LDAP Injection Protection**

**Severity:** MEDIUM  
**Affected Files:** 4 files
- Build_User_CSF_Phone_From_Template_v1.py (line 56)
- Decommission_User_CSF_Voicemail_v1.py (line 41)
- Update_AD_Phone_Fields_v1.py (lines 30-33, 98-103)

**Pattern:**
```python
q = adquery.ADQuery()
q.execute_query(
    attributes=["distinguishedName", "sAMAccountName"],
    where_clause=f"sAMAccountName = '{safe}'",
)
```

**Current Status:** GOOD

Update_AD_Phone_Fields_v1.py includes LDAP filter escaping:
```python
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
```

**Recommendation:** Apply same escaping to other scripts that accept user input for LDAP queries.

---

### 7. **MEDIUM: Output File Permission Issues**

**Severity:** MEDIUM  
**Affected Files:** All files that write to `output_logs/` directory

**Pattern:**
```python
output_dir = 'output_logs'
log_filename = os.path.join(output_dir, f"extract_all_phones_{current_time}.csv")
with open(log_filename, 'w', newline='', encoding='utf-8') as logfile:
```

**Risk:**
- Output files may contain sensitive data (usernames, extensions, configuration)
- File permissions inherited from parent directory (often world-readable on shared systems)
- Timestamps in filenames predictable
- No protection against directory traversal attacks

**Recommendation:**
```python
import os
import stat

def create_secure_log_file(log_filename):
    """Create log file with restricted permissions (user only)."""
    # Create file with restrictive permissions
    old_umask = os.umask(0o077)  # rwx------ for file
    try:
        logfile = open(log_filename, 'w', newline='', encoding='utf-8')
    finally:
        os.umask(old_umask)
    
    # Ensure output_logs directory exists with restricted permissions
    os.makedirs(os.path.dirname(log_filename), mode=0o700, exist_ok=True)
    
    return logfile
```

---

## Low-Severity Findings

### 8. **LOW: Error Messages May Leak Information**

**Severity:** LOW  
**Affected Files:** Multiple files

**Pattern:**
```python
except Exception as e:
    print(f"  ✗ Preview failed: {result['message']}")
```

**Risk:**
- Detailed error messages could reveal system information
- Stack traces may contain path information or API details

**Recommendation:**
```python
# Log details internally for debugging
logger.debug(f"Full error: {e}", exc_info=True)

# Show user-friendly message
print("  ✗ Operation failed. Check logs for details.")
```

---

### 9. **LOW: Magic Numbers and Hardcoded Values**

**Severity:** LOW  
**Affected Files:** Multiple files

**Pattern:**
```python
DEFAULT_VM_PIN = "56219"
LAB_CUCM_IP = "lascucmpl01.ahs.int"
PROD_CUCM_IP = "lascucmpp01.ahs.int"
```

**Recommendation:**
Move configuration to external config file or environment variables. Consider using `.env` file (excluded from git) for secrets.

---

### 10. **LOW: Missing input() Echo Control**

**Severity:** LOW  
**Affected Files:** Multiple files

**Pattern:**
```python
ad_password = getpass.getpass("  AD Password: ")  # Good - hides input
new_pin = input("Enter new voicemail PIN (visible): ").strip()  # Shows input
```

**Recommendation:**
For PIN entry, consider using getpass:
```python
new_pin = getpass.getpass("Enter new voicemail PIN (hidden): ").strip()
```

---

## Summary Table

| Finding | Severity | Count | Status |
|---------|----------|-------|--------|
| Disabled SSL Verification | CRITICAL | 17 files | Not Fixed |
| XXE Vulnerability (XML) | HIGH | 12 files | Not Fixed |
| Subprocess Risk | HIGH | 2 files | Low Risk (mitigated) |
| Credential Handling | HIGH | 1 file | FIXED |
| Input Validation | MEDIUM | 20 files | Partial |
| LDAP Injection | MEDIUM | 4 files | Mitigated (1 file) |
| File Permissions | MEDIUM | 20 files | Not Fixed |
| Info Leakage | LOW | Many | Not Fixed |
| Config Hardcoding | LOW | Multiple | Not Fixed |

---

## Required Remediation Actions

### Phase 1: CRITICAL (Must fix before production use)
1. **Enable SSL Verification** on all 17 affected files
   - Change `session.verify = False` to `session.verify = True`
   - Remove `urllib3.disable_warnings()` calls
   - Provide proper CA certificates for lab/dev servers

2. **Add XXE Protection** on all 12 affected files
   - Install defusedxml: `pip install defusedxml`
   - Replace `ET` imports with `defusedxml.ElementTree`

### Phase 2: HIGH (Should fix soon)
3. **Apply LDAP Filter Escaping** to all user input in LDAP queries
4. **Implement Input Validation** for all user prompts
5. **Secure Output Files** with restrictive permissions

### Phase 3: MEDIUM (Recommended)
6. **Review Error Handling** to avoid information leakage
7. **Externalize Configuration** (IPs, credentials references)
8. **Add Request Logging** for audit trails

---

## Files Already Hardened

✅ **Update_AD_Phone_Fields_v1.py**
- Credentials stored locally, not globally
- Credentials scrubbed in finally block
- LDAP filter escaping implemented
- Uses subprocess.run safely (list form, not shell=True)

---

## Testing Recommendations

1. **Test SSL Verification:**
   ```bash
   # Verify CUCM/Unity certs are valid
   openssl s_client -connect lascucmpp01.ahs.int:8443 < /dev/null
   ```

2. **Test XXE Protection:**
   - Attempt XXE payload in XML responses (in dev environment only)
   - Verify defusedxml blocks expansion

3. **Test Input Validation:**
   - Try SQL/LDAP injection characters in inputs
   - Test boundary conditions (empty, max length, special chars)

---

## References

- [OWASP Top 10 - A02:2021 Cryptographic Failures](https://owasp.org/Top10/A02_2021-Cryptographic_Failures/)
- [OWASP Top 10 - A05:2021 Security Misconfiguration](https://owasp.org/Top10/A05_2021-Security_Misconfiguration/)
- [CWE-295: Improper Certificate Validation](https://cwe.mitre.org/data/definitions/295.html)
- [CWE-611: Improper Restriction of XML External Entity Reference](https://cwe.mitre.org/data/definitions/611.html)
- [defusedxml Documentation](https://github.com/tiran/defusedxml)
- [Python getpass Module](https://docs.python.org/3/library/getpass.html)

---

**Report Prepared By:** GitHub Copilot  
**Recommended Review:** Information Security Team  
**Next Review Date:** After critical fixes are implemented
