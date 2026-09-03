"""Role-Based Access Control (RBAC) for the enterprise RAG platform.

Roles (least to most privileged):
  readonly   → can query only
  user       → can query + ingest personal docs
  org_admin  → user + manage org users, view audit logs
  super_admin → all permissions

Permissions are evaluated at the FastAPI dependency level via ``require_role``.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from fastapi import HTTPException, status


class Role(str, Enum):
    READONLY = "readonly"
    USER = "user"
    ORG_ADMIN = "org_admin"
    SUPER_ADMIN = "super_admin"


# Role hierarchy: higher index == more privileged
_ROLE_HIERARCHY: dict[str, int] = {
    Role.READONLY: 0,
    Role.USER: 1,
    Role.ORG_ADMIN: 2,
    Role.SUPER_ADMIN: 3,
}


def _level(role: str) -> int:
    return _ROLE_HIERARCHY.get(role, -1)


def has_role(user_role: str, required_role: str) -> bool:
    """Return True if ``user_role`` has at least ``required_role`` privilege."""
    return _level(user_role) >= _level(required_role)


def require_role(
    required_role: str,
    user_role: Optional[str],
    detail: str = "Insufficient permissions",
) -> None:
    """Raise HTTP 403 if ``user_role`` does not meet ``required_role``."""
    if user_role is None or not has_role(user_role, required_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


def require_same_org(
    request_org_id: str,
    user_org_id: str,
    user_role: str,
    detail: str = "Cross-tenant access denied",
) -> None:
    """Ensure the user can only access their own org data unless super_admin."""
    if has_role(user_role, Role.SUPER_ADMIN):
        return
    if request_org_id != user_org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


# Convenience permission checks used in route handlers

class Permission:
    """Static permission check helpers."""

    @staticmethod
    def can_ingest(role: str) -> bool:
        return has_role(role, Role.USER)

    @staticmethod
    def can_delete_document(role: str) -> bool:
        return has_role(role, Role.USER)

    @staticmethod
    def can_delete_all_documents(role: str) -> bool:
        return has_role(role, Role.ORG_ADMIN)

    @staticmethod
    def can_manage_users(role: str) -> bool:
        return has_role(role, Role.ORG_ADMIN)

    @staticmethod
    def can_manage_org(role: str) -> bool:
        return has_role(role, Role.ORG_ADMIN)

    @staticmethod
    def can_view_audit_logs(role: str) -> bool:
        return has_role(role, Role.ORG_ADMIN)

    @staticmethod
    def can_access_admin(role: str) -> bool:
        return has_role(role, Role.SUPER_ADMIN)

    @staticmethod
    def can_query(role: str) -> bool:
        return has_role(role, Role.READONLY)
