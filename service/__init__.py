"""HTTP wrapper around the clinical engine.

This package adds no clinical logic. Every route delegates to a function that already
existed and was already tested; what lives here is JSON in, JSON out, and the mapping of
the engine's exceptions onto status codes.

Nothing in this package persists anything. See `service.app` for why.
"""
