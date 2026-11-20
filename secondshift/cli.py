"""The `secondshift` command. It runs Django management commands.

Installed, it defaults to the local environment (`secondshift up`). A
checkout's manage.py defaults to dev.
"""

import sys
from importlib.metadata import version

from django.core.management import execute_from_command_line

from secondshift.config.app import select_settings
from secondshift.config.constants import Environment


def main(default_environment: Environment = Environment.LOCAL) -> None:
    # Django answers these with its own version, which says nothing about SecondShift.
    if sys.argv[1:] in (["--version"], ["version"]):
        print(version("secondshift"))
        return
    select_settings(default_environment)
    execute_from_command_line(sys.argv)
