"""Infrastructure layer: concrete adapters to the outside world.

This is the only layer allowed to know exchange formats and storage technology.
Planned adapter packages are ``gpx/``, ``fit/``, ``tcx/``, ``filesystem/`` and
``database/``. None exists yet.

A format adapter ends at a clear normalization boundary: it turns raw source bytes
into the canonical normalized track model plus source metadata, and its own
format-specific structures (XML elements, FIT messages) must not travel any
further into the application.

Adapters implement contracts declared by the domain and application layers; they
must not import the API layer.
"""
