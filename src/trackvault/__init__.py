"""TrackVault: a self-hosted archive for planned and recorded activity tracks.

The package version is resolved from the installed distribution metadata so that
``pyproject.toml`` stays the single source of truth for the version number.
``trackvault.release`` holds it, so layers that need it can read it without
importing the package root.
"""

from trackvault.release import VERSION

__all__ = ["__version__"]

__version__ = VERSION
