"""How to start Codex with only the run's broker and the files beside it."""

from pathlib import Path

from secondshift.agent.image import outcomes
from secondshift.agent.image.outcomes import AgentConfig, fill_template, private_file

BINARY = "codex"
HERE = Path(__file__).parent
BROKER_TOKEN_VAR = "SECONDSHIFT_BROKER_TOKEN"
BROKER_STARTUP_TIMEOUT_SECONDS = 60


def start(config: AgentConfig) -> tuple[list[str], dict[str, str]]:
    """Codex with no built-in tools and only the run's broker.

    A login check has no broker, so it has no tools at all.
    """
    settings = (HERE / "config.toml").read_text()
    if config.get("broker_url"):
        settings += fill_template(
            HERE / "broker.toml",
            startup_timeout=BROKER_STARTUP_TIMEOUT_SECONDS,
            url=config["broker_url"],
            token_var=BROKER_TOKEN_VAR,
        )
    # Codex keeps its refreshed login in CODEX_HOME, which is /auth, so its
    # config must live there too. It is rewritten on every run, so nothing a
    # previous run wrote survives.
    private_file(outcomes.AUTH / "config.toml", settings)
    argv = [BINARY, "exec", "--skip-git-repo-check", "--ephemeral"]
    argv += ["--strict-config", "--json", "-"]
    env = {BROKER_TOKEN_VAR: config["broker_token"]} if config.get("broker_url") else {}
    return argv, env
