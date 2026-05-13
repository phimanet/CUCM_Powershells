# CLAUDE Project Tracker

Last Updated: 2026-04-30

## Project Mission
Maintain and improve Cisco CUCM and Unity automation scripts in this repository, with clear operational runbooks and safe repeatable execution.

## Ongoing Goals
- Standardize script behavior and logging across CUCM/Unity workflows.
- Reduce manual prep for onboarding and decommissioning tasks.
- Keep templates and generated outputs organized and predictable.
- Document repeatable operating procedures for daily execution.

## Pending Tasks
- [ ] Create durable project context files for faster future sessions.
- [ ] Add script inventory with purpose, inputs, and outputs.
- [ ] Document shared configuration assumptions (server, auth, environment).
- [ ] Define a common error-handling and retry pattern for API calls.

## Key Decisions
- 2026-04-30: Use this CLAUDE.md file as the central source of truth for ongoing goals, pending tasks, and decisions.
- 2026-04-30: Keep updates concise and append-only by date when possible.
- 2026-04-30: This workbook is PowerShell-only for operational work. Python is used here for prototyping/validation, then promoted to a separate workbook for web implementation.
- 2026-04-30: User manually moves approved files between workbooks; do not automate file transfer as part of this repo workflow.
- 2026-04-30: Do not provide or run Ubuntu deployment/update commands (`cd /opt/cucm-web`, `git pull`, `systemctl restart`) in this workbook.
- 2026-04-30: Scripts in this repo are Python scripts executed from PowerShell only.
- 2026-04-30: Delivery workflow is GitHub save/upload only, with explicit change tracking for each update.
- 2026-04-30: "Save and push" means save changed files and upload to GitHub via git push.

## Current Focus
- Establishing durable project memory and workflow context in this repo.
- Keep script guidance aligned to a PowerShell-first operating model in this workspace.

## Session Notes
- 2026-04-30: Initialized tracker structure for ongoing collaboration.
- 2026-04-30: User clarified workflow: this workspace remains PowerShell-focused; successful Python logic is handed off to another workspace for web conversion.
- 2026-04-30: User confirmed that cross-workbook file movement is manual.
- 2026-04-30: User explicitly excluded Ubuntu update/restart steps from this workbook.
- 2026-04-30: User confirmed execution model: Python scripts are run via PowerShell only.
- 2026-04-30: User requested GitHub-only save/upload workflow and active change tracking.
- 2026-04-30: User clarified that "save and push" explicitly means upload changes to GitHub.
- 2026-04-30: User reported Add_Secondary_TCT_Device_v1.py did not create the phone; hold conversation context for follow-up troubleshooting.

## Change Tracking
- 2026-04-30: Initialized change-tracking section in central project tracker.
- 2026-04-30: Added and pushed template-driven TCT secondary device workflow; post-push user reported Add_Secondary_TCT_Device_v1.py failed to create the phone.

## Next Actions
- Confirm preferred priority order for pending tasks.
- Start with context-file scaffolding or script inventory, based on user choice.
- Troubleshoot Add_Secondary_TCT_Device_v1.py creation failure using the latest output log and CUCM AXL fault details.
