import os
import subprocess
import sys

def clear_screen():
    # Clears the terminal screen for a cleaner look (works on Windows and Mac/Linux)
    os.system('cls' if os.name == 'nt' else 'clear')

def run_script(script_name):
    """Checks if the script exists, then runs it using the current Python executable."""
    if not os.path.exists(script_name):
        print(f"\n[ERROR] Could not find '{script_name}' in the current folder.")
        print("Please make sure all your scripts are saved in the same directory.")
        input("\nPress Enter to continue...")
        return
    
    print(f"\n{'='*40}")
    print(f" Executing: {script_name}")
    print(f"{'='*40}\n")
    
    try:
        # sys.executable ensures it uses the same python version running the menu
        subprocess.run([sys.executable, script_name])
    except Exception as e:
        print(f"\n[ERROR] An exception occurred while running the script: {e}")
    
    print(f"\n{'='*40}")
    print(f" Finished: {script_name}")
    print(f"{'='*40}")
    input("\nPress Enter to return to the main menu...")

def main():
    while True:
        clear_screen()
        print("==================================================")
        print("           CUCM AXL Automation Toolkit            ")
        print("==================================================")
        print("\n--- Directory Numbers ---")
        print("  1. Add Directory Numbers")
        print("     (Requires: patterns.csv)")
        print("  2. Extract Specific Directory Numbers")
        print("     (Requires: patterns.csv)")
        print("  3. Extract ALL Directory Numbers")
        
        print("\n--- Translation Patterns ---")
        print("  4. Add Translation Patterns")
        print("     (Requires: translation_patterns_insert_template.csv)")
        print("  5. Extract ALL Translation Patterns")

        print("\n--- End Users ---")
        print("  6. Extract End User by Last Name")

        print("\n--- Phone Devices ---")
        print("  8. Extract ALL Phone Devices")
        print("  9. Build CSF Phone From Template")
        
        print("\n--- Server Tools ---")
        print("  7. Do LDAP User Sync")

        print("\n--------------------------------------------------")
        print("  0. Exit")
        print("==================================================")
        
        choice = input("\nEnter your choice (0-9): ")
        
        if choice == '1':
            run_script("Add_DirectoryNumber_v1.py")
        elif choice == '2':
            run_script("Extract_DirectoryNumber_v1.py")
        elif choice == '3':
            run_script("Extract_All_DirectoryNumber_v1.py")
        elif choice == '4':
            run_script("Add_TranslationsPattern_v1.py")
        elif choice == '5':
            run_script("Extract_All_TranslationsPattern_v1.py")
        elif choice == '6':
            run_script("Extract_EndUser_v1.py")
        elif choice == '7':
            run_script("CUCM_LDAP_Sync_v1.py")
        elif choice == '8':
            run_script("Extract_All_Phones_v1.py")
        elif choice == '9':
            run_script("Build_User_CSF_Phone_From_Template_v1.py")
        elif choice == '0':
            clear_screen()
            print("Exiting Toolkit. Goodbye!")
            break
        else:
            print("\nInvalid choice. Please enter a number between 0 and 9.")
            input("Press Enter to try again...")

if __name__ == "__main__":
    # Ensure the script runs in the directory where it's located
    os.chdir(os.path.dirname(os.path.abspath(__file__)) or '.')
    main()