"""How to start Claude Code with only the run's broker and the files beside it."""

import shlex
import sys
from pathlib import Path

from secondshift.agent.image import outcomes
from secondshift.agent.image.claude.subagent_hook import AGENT_TOOLS, WORKER
from secondshift.agent.image.outcomes import (
    AUTH_FILES,
    AgentConfig,
    fill_template,
    private_file,
)

BINARY = "claude"
HERE = Path(__file__).parent
HOOK = HERE / "subagent_hook.py"
WORKER_PROMPT = (HERE / "worker_prompt.md").read_text()
# The broker's name in Claude Code's MCP config, and the tools it serves.
SERVER = "secondshift"
BROKER_TOOLS = f"mcp__{SERVER}__*"


def subagent_options(limit: int) -> list[str]:
    """The Agent tool, one server-owned worker with only the broker's tools,
    and the hook that refuses any other start."""
    agents = fill_template(
        HERE / "agents.json", worker=WORKER, prompt=WORKER_PROMPT, tools=BROKER_TOOLS
    )
    command = shlex.join(
        [sys.executable, str(HOOK), str(limit), str(outcomes.TMP / "subagents")]
    )
    settings = fill_template(
        HERE / "settings.json", matcher="|".join(AGENT_TOOLS), command=command
    )
    private_file(outcomes.TMP / "settings.json", settings)
    return ["--agents", agents, "--settings", str(outcomes.TMP / "settings.json")]


def start(config: AgentConfig) -> tuple[list[str], dict[str, str]]:
    """Claude Code with no built-in tools and only the run's broker.

    A run with sub-agents also gets the Agent tool. A login check has no
    broker, so it has no tools at all.
    """
    subagents = int(config.get("subagents", 0)) if config.get("broker_url") else 0
    argv = [
        BINARY,
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--tools",
        AGENT_TOOLS[0] if subagents else "",
        "--strict-mcp-config",
    ]
    if subagents:
        argv += subagent_options(subagents)
    if config.get("broker_url"):
        servers = fill_template(
            HERE / "mcp.json",
            server=SERVER,
            url=config["broker_url"],
            authorization=f"Bearer {config['broker_token']}",
        )
        private_file(outcomes.TMP / "mcp.json", servers)
        argv += [
            "--mcp-config",
            str(outcomes.TMP / "mcp.json"),
            "--allowedTools",
            BROKER_TOOLS,
        ]
    argv += [
        "--max-turns",
        str(config["max_turns"]),
        "--setting-sources",
        "",
        "--no-session-persistence",
    ]
    token = (outcomes.AUTH / AUTH_FILES["claude_code"]).read_text().strip()
    return argv, {"CLAUDE_CODE_OAUTH_TOKEN": token}
