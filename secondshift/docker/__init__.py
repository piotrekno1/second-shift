"""Ask Docker about itself and its images, for the command line and the runner.

The runner builds its sandbox command lines itself, next to the checks that
keep repository input out of them. This package holds only the questions and
builds that several parts share. It imports nothing else from SecondShift.
"""
