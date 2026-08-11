"""GPX-View: a local web application for planned and recorded GPS tracks.

The package version is resolved from the installed distribution metadata so that
``pyproject.toml`` stays the single source of truth for the version number.
``gpx_view.release`` holds it, so layers that need it can read it without
importing the package root.
"""

from gpx_view.release import VERSION

__all__ = ["__version__"]

__version__ = VERSION
