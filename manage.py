#!/usr/bin/env python
from secondshift.cli import main
from secondshift.config.constants import Environment

if __name__ == "__main__":
    main(default_environment=Environment.DEV)
