"""Convenience shim: `python main.py run` == `python -m geojobbot run`."""
import sys

from geojobbot.main import main

if __name__ == "__main__":
    sys.exit(main())
