import getpass
import json
import re
import subprocess

PYAD_IMPORT_ERROR = None

try:
    import pyad.adbase
    from pyad import aduser, adquery
    PYAD_AVAILABLE = True
except Exception as e:
    PYAD_AVAILABLE = False
    PYAD_IMPORT_ERROR = str(e)


def _format_ad_error(exc):
    """Return a friendlier AD/pyad runtime error message."""
    message = str(exc).strip()
    if "Microsoft OLE DB Service Components" in message and "The parameter is incorrect" in message:
        return (
            "pyad AD query failed while opening the ADODB provider. "
            "This usually happens when pyad is forced to use alternate credentials. "
            "Run the script with your current Windows/domain login instead of entering AD credentials."
        )
    return message


def _run_ad_query(attributes, where_clause):
    """Execute an AD query and normalize provider errors into readable messages."""
    try:
        q = adquery.ADQuery()
        q.execute_query(attributes=attributes, where_clause=where_clause)
        return list(q.get_results())
    except Exception as exc:
        raise RuntimeError(_format_ad_error(exc)) from exc


def _lookup_ad_user_via_powershell(samaccountname, auth_context):
    """Query AD with alternate credentials using the ActiveDirectory PowerShell module."""
    payload = {
        "username": auth_context["username"],
        "password": auth_context["password"],
        "samaccountname": samaccountname,
    }
    script = r"""
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
Import-Module ActiveDirectory -ErrorAction Stop
$secure = ConvertTo-SecureString $payload.password -AsPlainText -Force
$cred = [System.Management.Automation.PSCredential]::new($payload.username, $secure)
$filterValue = [string]$payload.samaccountname
$user = Get-ADUser -Credential $cred -LDAPFilter "(sAMAccountName=$filterValue)" -Properties telephoneNumber, ipPhone, distinguishedName
if ($null -eq $user) {
    @{ found = $false } | ConvertTo-Json -Compress
    exit 0
}
@{
    found = $true
    distinguishedName = [string]$user.DistinguishedName
    telephoneNumber = [string]$user.telephoneNumber
    ipPhone = [string]$user.ipPhone
} | ConvertTo-Json -Compress
"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        raise RuntimeError(f"PowerShell AD lookup failed to start: {exc}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"PowerShell exited with code {completed.returncode}"
        raise RuntimeError(f"Alternate-credential AD lookup failed: {detail}")

    output = (completed.stdout or "").strip()
    if not output:
        raise RuntimeError("Alternate-credential AD lookup returned no data")

    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Alternate-credential AD lookup returned unreadable output: {output}") from exc

    return {
        "found": bool(data.get("found")),
        "distinguishedName": (data.get("distinguishedName") or "").strip(),
        "telephoneNumber": (data.get("telephoneNumber") or "").strip(),
        "ipPhone": (data.get("ipPhone") or "").strip(),
    }


def _update_ad_user_via_powershell(samaccountname, auth_context, telephone_number=None, ip_phone=None, clear=False):
    """Update AD phone attributes with alternate credentials using PowerShell."""
    payload = {
        "username": auth_context["username"],
        "password": auth_context["password"],
        "samaccountname": samaccountname,
        "telephoneNumber": telephone_number,
        "ipPhone": ip_phone,
        "clear": clear,
    }
    script = r"""
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
Import-Module ActiveDirectory -ErrorAction Stop
$secure = ConvertTo-SecureString $payload.password -AsPlainText -Force
$cred = [System.Management.Automation.PSCredential]::new($payload.username, $secure)
$filterValue = [string]$payload.samaccountname
$user = Get-ADUser -Credential $cred -LDAPFilter "(sAMAccountName=$filterValue)" -Properties telephoneNumber, ipPhone
if ($null -eq $user) {
    throw "AD user '$filterValue' not found"
}
if ([bool]$payload.clear) {
    Set-ADUser -Credential $cred -Identity $user.DistinguishedName -Clear telephoneNumber, ipPhone -ErrorAction Stop
}
else {
    Set-ADUser -Credential $cred -Identity $user.DistinguishedName -Replace @{
        telephoneNumber = [string]$payload.telephoneNumber
        ipPhone = [string]$payload.ipPhone
    } -ErrorAction Stop
}
@{ success = $true } | ConvertTo-Json -Compress
"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        raise RuntimeError(f"PowerShell AD update failed to start: {exc}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"PowerShell exited with code {completed.returncode}"
        raise RuntimeError(f"Alternate-credential AD update failed: {detail}")

    output = (completed.stdout or "").strip()
    if output:
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Alternate-credential AD update returned unreadable output: {output}") from exc
        if not data.get("success"):
            raise RuntimeError("Alternate-credential AD update did not report success")


def _get_ad_user_record(samaccountname, auth_context=None):
    """Return a normalized AD user record for the target sAMAccountName."""
    safe = _escape_ldap_filter(samaccountname)
    auth_mode = (auth_context or {}).get("mode", "integrated")
    if auth_mode == "alternate":
        return _lookup_ad_user_via_powershell(safe, auth_context)

    results = _run_ad_query(
        attributes=["distinguishedName", "sAMAccountName", "telephoneNumber", "ipPhone"],
        where_clause=f"sAMAccountName = '{safe}'",
    )
    if not results:
        return {"found": False, "distinguishedName": "", "telephoneNumber": "", "ipPhone": ""}
    if len(results) > 1:
        print(f"  WARNING: Multiple AD accounts matched '{samaccountname}'. Using the first result.")
    row = results[0]
    tel = row.get("telephoneNumber") or ""
    ipp = row.get("ipPhone") or ""
    if isinstance(tel, list):
        tel = tel[0] if tel else ""
    if isinstance(ipp, list):
        ipp = ipp[0] if ipp else ""
    return {
        "found": True,
        "distinguishedName": (row.get("distinguishedName") or "").strip(),
        "telephoneNumber": str(tel).strip(),
        "ipPhone": str(ipp).strip(),
    }

# ─────────────────────────────────────────
# AD HELPER FUNCTIONS
# (shared logic — also embedded in Build and Decommission scripts)
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


def _format_phone_dashes(phone):
    """Format 10-digit number as 999-999-9999 for telephoneNumber."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"
    return phone


def _format_phone_plain(phone):
    """Return digits-only 10-digit number for ipPhone."""
    digits = re.sub(r"\D", "", phone)
    return digits if len(digits) == 10 else phone


def _find_ad_user(samaccountname, auth_context=None):
    """Look up AD user by sAMAccountName. Returns ADUser or None."""
    record = _get_ad_user_record(samaccountname, auth_context=auth_context)
    if record["found"] and record["distinguishedName"]:
        return aduser.ADUser.from_dn(record["distinguishedName"])
    return None


def _read_ad_phone_fields(samaccountname, auth_context=None):
    """
    Read back telephoneNumber and ipPhone directly from AD.
    Returns dict with current values (empty string if not set).
    """
    record = _get_ad_user_record(samaccountname, auth_context=auth_context)
    if not record["found"]:
        return {"telephoneNumber": "(user not found)", "ipPhone": "(user not found)"}
    return {
        "telephoneNumber": record["telephoneNumber"],
        "ipPhone": record["ipPhone"],
    }


def update_ad_phone_fields(samaccountname, phone_number, auth_context=None):
    """
    Set telephoneNumber (dashes) and ipPhone (digits) for the given sAMAccountName.
    Performs actual write to AD.
    Returns dict with keys: success, telephone, ipphone, message.
    """
    auth_mode = (auth_context or {}).get("mode", "integrated")
    if auth_mode != "alternate" and not PYAD_AVAILABLE:
        detail = f": {PYAD_IMPORT_ERROR}" if PYAD_IMPORT_ERROR else ""
        return {"success": False, "message": f"pyad unavailable{detail}"}
    try:
        phone_dashes = _format_phone_dashes(phone_number)
        phone_plain  = _format_phone_plain(phone_number)
        if auth_mode == "alternate":
            _update_ad_user_via_powershell(
                samaccountname,
                auth_context,
                telephone_number=phone_dashes,
                ip_phone=phone_plain,
                clear=False,
            )
        else:
            user = _find_ad_user(samaccountname, auth_context=auth_context)
            if not user:
                return {"success": False, "message": f"AD user '{samaccountname}' not found"}
            user.update_attribute("telephoneNumber", phone_dashes)
            user.update_attribute("ipPhone", phone_plain)
        return {
            "success": True,
            "telephone": phone_dashes,
            "ipphone": phone_plain,
            "message": "Updated successfully",
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


def clear_ad_phone_fields(samaccountname, auth_context=None):
    """
    Remove telephoneNumber and ipPhone from the AD user account.
    Performs actual write to AD.
    Returns dict with keys: success, message.
    """
    auth_mode = (auth_context or {}).get("mode", "integrated")
    if auth_mode != "alternate" and not PYAD_AVAILABLE:
        detail = f": {PYAD_IMPORT_ERROR}" if PYAD_IMPORT_ERROR else ""
        return {"success": False, "message": f"pyad unavailable{detail}"}
    try:
        if auth_mode == "alternate":
            _update_ad_user_via_powershell(samaccountname, auth_context, clear=True)
        else:
            user = _find_ad_user(samaccountname, auth_context=auth_context)
            if not user:
                return {"success": False, "message": f"AD user '{samaccountname}' not found"}
            user.clear_attribute("telephoneNumber")
            user.clear_attribute("ipPhone")
        return {"success": True, "message": "Cleared successfully"}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ─────────────────────────────────────────
# STANDALONE TEST ENTRYPOINT
# ─────────────────────────────────────────

def main():
    auth_context = {"mode": "integrated", "username": None, "password": None}

    try:
        print("=" * 60)
        print("  AD PHONE FIELD UPDATER")
        print("=" * 60 + "\n")

        print("NOTICE: This script will perform actual AD write operations.")
        print("All selected phone field updates will be committed to Active Directory.\n")

        if not PYAD_AVAILABLE:
            print("ERROR: pyad is unavailable.")
            if PYAD_IMPORT_ERROR:
                print(f"  Detail: {PYAD_IMPORT_ERROR}")
            print("  Install/repair dependencies:")
            print("    pip install pywin32 pyad")
            return

        # ── Authentication mode ──────────────────────────────────
        print("AD queries work best with your current Windows/domain login.")
        print("Use alternate credentials only if you know this machine requires them.")
        use_alt_creds = input("  Use alternate AD credentials? (y/N): ").strip().lower()

        if use_alt_creds in {"y", "yes"}:
            ad_username = input("  AD Username (DOMAIN\\user or user@domain): ").strip()
            if not ad_username:
                print("No username provided. Exiting.")
                return
            ad_password = getpass.getpass("  AD Password: ")
            auth_context = {"mode": "alternate", "username": ad_username, "password": ad_password}
            print("  ✓ Alternate credentials stored for PowerShell AD lookup\n")
        else:
            pyad.adbase.set_defaults(username=None, password=None)
            print("  ✓ Using current Windows/domain login for AD query\n")

        # ── Target user ───────────────────────────────────────────
        target_sam = input("Enter sAMAccountName of the test user (e.g., first.last): ").strip()
        if not target_sam:
            print("No target user entered. Exiting.")
            return

        # Show current field values before making any changes
        print(f"\nCurrent AD values for '{target_sam}':")
        try:
            before = _read_ad_phone_fields(target_sam, auth_context=auth_context)
        except Exception as e:
            print(f"  ✗ AD lookup failed: {_format_ad_error(e)}")
            return
        print(f"  telephoneNumber : {before['telephoneNumber'] or '(empty)'}")
        print(f"  ipPhone         : {before['ipPhone'] or '(empty)'}")

        # ── Action menu ───────────────────────────────────────────
        print("\nActions:")
        print("  1 - SET   telephoneNumber and ipPhone")
        print("  2 - CLEAR telephoneNumber and ipPhone")
        print("  0 - Exit without changes")
        action = input("\nEnter choice (0, 1, or 2): ").strip()

        if action == "0":
            print("No changes made.")
            return

        if action not in {"1", "2"}:
            print("Invalid choice. No changes made.")
            return

        # ── Action 1: Set Phone Fields ──────────────────────────────
        if action == "1":
            phone_raw = input("Enter 10-digit phone number (e.g., 4695551234): ").strip()
            digits = re.sub(r"\D", "", phone_raw)
            if len(digits) != 10:
                print(f"  ✗ Invalid — must be exactly 10 digits. Got: '{phone_raw}'")
                return

            print(f"\nUpdating AD phone fields for '{target_sam}'...")
            result = update_ad_phone_fields(target_sam, phone_raw, auth_context=auth_context)

            if result["success"]:
                print("  ✓ AD fields updated successfully")
            else:
                print(f"  ✗ Update failed: {result['message']}")
                return

            print("\nAD field changes applied:")
            print(f"  telephoneNumber : {before['telephoneNumber'] or '(empty)'}  ->  {result['telephone']}")
            print(f"  ipPhone         : {before['ipPhone'] or '(empty)'}  ->  {result['ipphone']}")
            print(f"\n  RESULT: LIVE UPDATE — {result['message']}")

        # ── Action 2: Clear Phone Fields ────────────────────────────
        elif action == "2":
            print(f"\nClearing AD phone fields for '{target_sam}'...")
            result = clear_ad_phone_fields(target_sam, auth_context=auth_context)

            if result["success"]:
                print("  ✓ AD fields cleared successfully")
            else:
                print(f"  ✗ Clear failed: {result['message']}")
                return

            print("\nAD field changes applied:")
            print(f"  telephoneNumber : {before['telephoneNumber'] or '(empty)'}  ->  (empty)")
            print(f"  ipPhone         : {before['ipPhone'] or '(empty)'}  ->  (empty)")
            print(f"\n  RESULT: LIVE CLEAR — {result['message']}")
    finally:
        auth_context["username"] = None
        auth_context["password"] = None


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
    except Exception as e:
        print(f"\nUnhandled error: {_format_ad_error(e)}")

