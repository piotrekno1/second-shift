"""The agent image: the container that runs a subscription CLI for one run.

`image/` holds the files the image ships. They use only the standard library,
because the image has nothing else installed. Each CLI has its own package
there, with its command, its event reader and its config files. The runner starts the image, and
the control plane reads the outcomes and usage it reports. `build.py` builds
the image on this machine for `secondshift up` and development.
"""
