"""GitHub access: App and token credentials, the REST API, and git transport.

This package knows GitHub, not SecondShift's models. Callers pass plain
values such as an installation ID or a repository ID. The Django apps turn
their own rows into those values.
"""
