"""pytest configuration — sets up PYTHONPATH for the src layout."""
import sys
import os

src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
if src not in sys.path:
    sys.path.insert(0, src)
