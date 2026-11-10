"""Double-click to open the Krillion Solver GUI without a terminal window."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from krillion_gui import main

main()
