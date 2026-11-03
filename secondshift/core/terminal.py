"""Styles for the messages the local CLI prints itself.

Click drops the styles when output is not a terminal.
"""

import click


def heading(text: str) -> None:
    click.secho(text, bold=True)


def done(text: str, detail: str = "") -> None:
    """A finished step, with an optional dimmed detail."""
    line = f"{click.style('✓', fg='green', bold=True)} {text}"
    if detail:
        line += f"  {click.style(detail, dim=True)}"
    click.echo(line)


def note(text: str) -> None:
    """Progress and hints that matter less than the steps."""
    click.secho(text, dim=True)


def link(url: str) -> str:
    return click.style(url, fg="cyan", underline=True)
