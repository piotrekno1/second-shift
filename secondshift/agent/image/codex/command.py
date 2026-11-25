"""How to start Codex with only the run's broker and the files beside it."""

from pathlib import Path

from secondshift.agent.image import outcomes
from secondshift.agent.image.outcomes import (
    AUTH_FILES,
    AgentConfig,
    fill_template,
    private_file,
)

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
    home = codex_home()
    argv = [BINARY, "exec", "--skip-git-repo-check", "--ephemeral"]
    argv += [*outcomes.model_options(config), "--strict-config", "--json", "-"]
    env = {"CODEX_HOME": str(home)}
    if config.get("broker_url"):
        env[BROKER_TOKEN_VAR] = config["broker_token"]
    private_file(home / "config.toml", settings)
    return argv, env


def codex_home() -> Path:
    """This run's own Codex home, sharing only the login with the login's
    other runs.

    Codex keeps SQLite state, logs and memories in its home. Runs that share
    a login run at once in separate sandboxes, where SQLite's locks don't
    reach each other, and one run's state must not reach the next. So only
    auth.json is shared, through a link: Codex writes a refreshed login in
    place, so the refresh reaches the login's folder and later runs.
    """
    home = outcomes.TMP / "codex"
    home.mkdir(mode=0o700, exist_ok=True)
    link = home / AUTH_FILES["codex"]
    if not link.is_symlink():
        link.symlink_to(outcomes.AUTH / AUTH_FILES["codex"])
    return home
