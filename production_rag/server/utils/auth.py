"""Authentication mechanism for the API — backward-compatible shim.

This module re-exports the ``authenticated`` dependency from the new
enterprise auth layer (``production_rag.server.auth.dependencies``).

The new implementation supports three auth methods in priority order:
  1. JWT Bearer token  (``Authorization: Bearer <token>``)
  2. API Key header    (``X-API-Key: <key>``)
  3. Legacy shared secret (original behavior — unchanged)

All existing routers that import ``authenticated`` from this module
continue to work without modification.
"""

# mypy: ignore-errors
from production_rag.server.auth.dependencies import (  # noqa: F401
    Principal,
    authenticated,
    get_current_principal,
)

__all__ = ["authenticated", "get_current_principal", "Principal"]
