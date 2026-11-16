"""What the control plane and the runner agree on.

Both sides import this package, and it imports neither. It holds the
specs the control plane sends, the paths and limits both sides enforce, and
the layout of what a session hands back. Nothing here may let repository
input choose runtime flags, mounts, images or secret bindings.
"""
