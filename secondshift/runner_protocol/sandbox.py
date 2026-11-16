"""Where a sandbox finds its files, and how large they may be."""

# Git object formats supported by repository snapshots and execution plans.
COMMIT_SHA_PATTERN = r"[0-9a-f]{40}|[0-9a-f]{64}"
# Limits for the files of one commit that a run gets. The control plane checks
# them while it builds a snapshot, and the runner checks them again.
MAX_SOURCE_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_SOURCE_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_SOURCE_FILES = 200_000
# Limits for what a run leaves behind: the agent's file changes, and the files
# it saves in /outputs. The runner applies them, and the control plane again.
MAX_CHANGES_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_FILES = 100
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
# Earlier runs' saved files that one run gets back. Uploaded like a snapshot,
# so it stays under that limit even when the files do not compress.
MAX_HISTORY_FILES_BYTES = 48 * 1024 * 1024
# Where a sandbox has the repository's files. Commands start here.
SANDBOX_WORKSPACE_DIR = "/workspace"
# Where a sandbox finds the folder for files it wants to hand back.
SANDBOX_OUTPUTS_DIR = "/outputs"
# Where a sandbox finds the files its job's earlier runs saved. Read-only.
SANDBOX_HISTORY_DIR = "/history"
# Where a sandbox finds a skill from another repository. Read-only.
SANDBOX_SKILL_DIR = "/skill"
