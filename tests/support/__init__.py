"""Helpers the test suite builds its own inputs with.

Nothing here is production code and nothing here is imported by `src/`. It
exists so that tests can construct a *real* artifact -- a valid vector tile
package, a provider that answers over HTTP -- instead of asserting against a
mock of the thing they mean to test.
"""
