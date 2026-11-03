"""PyInstaller entry script. Kept outside the package so `videotutor` is
imported as a normal package, the same way `python -m videotutor` does."""
from videotutor.__main__ import run

run()
