"""Run git without the settings, hooks or environment of the machine it runs on.

Every part of SecondShift runs git through this package: the control plane,
the runner and the command line. It imports nothing else from SecondShift.
"""
