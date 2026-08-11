"""Which release this build is, importable from anywhere but the domain.

A leaf module holding one string. It exists because the version had become
reachable only from the composition root, and the first thing that genuinely
needed it further in was the User-Agent an outbound map download presents: a
provider giving away bandwidth should be able to tell which application and
which release is asking.

The value still resolves from the installed distribution metadata, so
``pyproject.toml`` remains the one authority for the number --
``tests/contract/test_release_metadata_contract.py`` keeps it that way. This
module adds a place to read it from, not a second place to state it.

The domain does not import this. A business rule that behaves differently in
one release is not a business rule.
"""

from importlib.metadata import version

VERSION = version("gpx-view")
"""The release this build is, read from the installed distribution."""

__all__ = ["VERSION"]
