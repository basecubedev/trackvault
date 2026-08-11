"""GPX-View: a local web application for planned and recorded GPS tracks.

The package version is resolved from the installed distribution metadata so that
``pyproject.toml`` stays the single source of truth for the version number.
"""

from importlib.metadata import version

__all__ = ["__version__"]

__version__ = version("gpx-view")
