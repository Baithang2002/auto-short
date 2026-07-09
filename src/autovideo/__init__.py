"""Production architecture foundation for the auto-short video platform.

The legacy scripts remain the runtime entrypoints during migration. New code
should depend on this package instead of importing from the monolithic scripts.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
