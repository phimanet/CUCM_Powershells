import argparse
import csv
import os
import datetime


def latest_extract_file(output_dir):
    candidates = [
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.startswith("extract_all_phones_") and f.endswith(".csv")
    ]
    if not candidates:
        return None
    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def value_from_row(row, keys):
    for key in keys:
        val = row.get(key, "")
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return ""


def find_phone_row_by_dn(rows, dn):
    dn = str(dn).strip()
    for row in rows:
        for key, val in row.items():
            if "dirn.pattern" in key and str(val).strip() == dn:
                return row
    return None


def main():
    parser = argparse.ArgumentParser(description="Build Add Phone CSV template from extracted phone data")
    parser.add_argument("--dn", help="Reference DN to clone settings from, e.g. 8585236620")
    parser.add_argument("--extract", help="Path to extract_all_phones CSV file")
    parser.add_argument("--output", help="Output CSV path for add phone template")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    output_logs = os.path.join(base_dir, "output_logs")

    extract_file = args.extract
    if not extract_file:
        extract_file = latest_extract_file(output_logs)

    if not extract_file or not os.path.exists(extract_file):
        print("Could not find an extract_all_phones CSV file.")
        print("Run Extract_All_Phones_v1.py first, or pass --extract with a file path.")
        return

    reference_dn = args.dn or input("Enter reference phone DN to clone (e.g. 8585236620): ").strip()
    if not reference_dn:
        print("No DN provided. Exiting.")
        return

    current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = args.output or os.path.join(base_dir, f"phone_add_insert_template_{reference_dn}_{current_time}.csv")

    print(f"Using extract file: {extract_file}")
    print(f"Searching for DN: {reference_dn}")

    with open(extract_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    source = find_phone_row_by_dn(rows, reference_dn)
    if source is None:
        print(f"No phone found with DN {reference_dn} in {extract_file}")
        return

    template_headers = [
        "Device Name",
        "Description",
        "Product",
        "Protocol",
        "Calling Search Space",
        "Device Pool",
        "Location",
        "MRGL",
        "Common Phone Profile",
        "Common Device Config",
        "Phone Button Template",
        "Softkey Template",
        "Security Profile Name",
        "SIP Profile Name",
        "Owner User ID",
        "Primary DN",
        "DN Route Partition",
        "Line Label",
        "Line Display",
        "Line Display ASCII",
        "Max Num Calls",
        "Busy Trigger",
        "Presence Group",
    ]

    template_row = {
        "Device Name": value_from_row(source, ["phone.name"]),
        "Description": value_from_row(source, ["phone.description"]),
        "Product": value_from_row(source, ["phone.product"]),
        "Protocol": value_from_row(source, ["phone.protocol"]),
        "Calling Search Space": value_from_row(source, ["phone.callingSearchSpaceName"]),
        "Device Pool": value_from_row(source, ["phone.devicePoolName"]),
        "Location": value_from_row(source, ["phone.locationName"]),
        "MRGL": value_from_row(source, ["phone.mediaResourceListName"]),
        "Common Phone Profile": value_from_row(source, ["phone.commonPhoneConfigName"]),
        "Common Device Config": value_from_row(source, ["phone.commonDeviceConfigName"]),
        "Phone Button Template": value_from_row(source, ["phone.phoneTemplateName", "phone.currentConfig.phoneTemplateName"]),
        "Softkey Template": value_from_row(source, ["phone.softkeyTemplateName", "phone.currentConfig.softkeyTemplateName"]),
        "Security Profile Name": value_from_row(source, ["phone.securityProfileName"]),
        "SIP Profile Name": value_from_row(source, ["phone.sipProfileName"]),
        "Owner User ID": value_from_row(source, ["phone.ownerUserName"]),
        "Primary DN": value_from_row(source, ["phone.lines.line[1].dirn.pattern", "phone.lines.line.dirn.pattern"]),
        "DN Route Partition": value_from_row(source, ["phone.lines.line[1].dirn.routePartitionName", "phone.lines.line.dirn.routePartitionName"]),
        "Line Label": value_from_row(source, ["phone.lines.line[1].label", "phone.lines.line.label"]),
        "Line Display": value_from_row(source, ["phone.lines.line[1].display", "phone.lines.line.display"]),
        "Line Display ASCII": value_from_row(source, ["phone.lines.line[1].displayAscii", "phone.lines.line.displayAscii"]),
        "Max Num Calls": value_from_row(source, ["phone.lines.line[1].maxNumCalls", "phone.lines.line.maxNumCalls"]),
        "Busy Trigger": value_from_row(source, ["phone.lines.line[1].busyTrigger", "phone.lines.line.busyTrigger"]),
        "Presence Group": value_from_row(source, ["phone.presenceGroupName"]),
    }

    with open(output_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=template_headers)
        writer.writeheader()
        writer.writerow(template_row)

    print("\nTemplate created successfully:")
    print(output_file)
    print("\nUpdate at least these fields before addPhone:")
    print("- Device Name")
    print("- Primary DN")
    print("- Owner User ID (if needed)")


if __name__ == "__main__":
    main()
