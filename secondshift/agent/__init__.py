"""The agent image: the container that runs a subscription CLI for one run.

`image/` holds the files the image ships. They use only the standard library,
because the image has nothing else installed. The runner starts the image, and
the control plane reads the outcomes and usage it reports. `build.py` builds
the image on this machine for `secondshift up` and development.
"""
