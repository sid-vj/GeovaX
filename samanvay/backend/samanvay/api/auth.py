"""Keycloak / OAuth 2.0 / OpenID Connect & RBAC + ABAC Spatial Authorization.

Enforces:
1. RBAC (Role-Based Access Control): SuperAdmin, StateDirector, DistrictCollector, Tahsildar, Citizen.
2. ABAC (Attribute-Based Access Control): Spatial jurisdiction matching against LGD (Local Government Directory)
   codes, District IDs, and Ward boundaries.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)


class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    STATE_DIRECTOR = "state_director"
    DISTRICT_COLLECTOR = "district_collector"
    TAHSILDAR = "tahsildar"
    SURVEY_OFFICER = "survey_officer"
    CITIZEN = "citizen"


@dataclass
class UserClaims:
    """Authenticated user context containing RBAC and ABAC spatial attributes."""
    user_id: str
    username: str
    roles: list[Role]
    # ABAC Spatial Scope:
    state_lgd: Optional[str] = None
    district_lgd: Optional[str] = None
    subdistrict_lgd: Optional[str] = None
    allowed_wards: list[str] = field(default_factory=list)
    is_super: bool = False

    def can_access_ward(self, ward: str) -> bool:
        """ABAC check for ward-level spatial data."""
        if self.is_super or Role.SUPER_ADMIN in self.roles or Role.STATE_DIRECTOR in self.roles:
            return True
        if not self.allowed_wards:
            # If no specific wards restricted, allowed within district scope
            return True
        return str(ward) in self.allowed_wards

    def can_access_district(self, district_code: str) -> bool:
        """ABAC check for district-level spatial data."""
        if self.is_super or Role.SUPER_ADMIN in self.roles or Role.STATE_DIRECTOR in self.roles:
            return True
        if self.district_lgd:
            return str(district_code) == str(self.district_lgd)
        return True


# Default demo mock profiles for Keycloak/OIDC testing & simulation
MOCK_USERS: dict[str, UserClaims] = {
    "token-superadmin": UserClaims(
        user_id="usr-001",
        username="nic_national_director",
        roles=[Role.SUPER_ADMIN],
        is_super=True,
    ),
    "token-tahsildar-egmore": UserClaims(
        user_id="usr-104",
        username="tahsildar_egmore_div",
        roles=[Role.TAHSILDAR, Role.SURVEY_OFFICER],
        state_lgd="33",
        district_lgd="571",
        subdistrict_lgd="057101",
        allowed_wards=["104", "105", "106", "Egmore", "Kilpauk"],
    ),
    "token-tahsildar-mylapore": UserClaims(
        user_id="usr-120",
        username="tahsildar_mylapore_div",
        roles=[Role.TAHSILDAR],
        state_lgd="33",
        district_lgd="571",
        subdistrict_lgd="057102",
        allowed_wards=["120", "121", "122", "Mylapore", "Alwarpet"],
    ),
    "token-citizen": UserClaims(
        user_id="usr-citizen-999",
        username="citizen_public",
        roles=[Role.CITIZEN],
        allowed_wards=[],
    ),
}

security = HTTPBearer(auto_error=False)


def decode_keycloak_jwt(token: str) -> dict[str, Any]:
    """Decode JWT token payload (compatible with Keycloak OIDC tokens)."""
    try:
        parts = token.split(".")
        if len(parts) == 3:
            # Standard JWT format: header.payload.signature
            payload_b64 = parts[1]
            # Handle base64 padding
            payload_b64 += "=" * (-len(payload_b64) % 4)
            decoded_bytes = base64.urlsafe_b64decode(payload_b64)
            return json.loads(decoded_bytes.decode("utf-8"))
    except Exception as err:
        logger.debug("Failed decoding raw JWT: %s", err)
    return {}


async def get_current_user(
    auth_header: Optional[HTTPAuthorizationCredentials] = Security(security),
    x_user_token: Optional[str] = Header(None, alias="X-Auth-Token"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    x_user_ward: Optional[str] = Header(None, alias="X-User-Ward"),
) -> UserClaims:
    """FastAPI dependency resolving user identity with Keycloak / Token / Header fallbacks."""
    token = None
    if auth_header and auth_header.credentials:
        token = auth_header.credentials
    elif x_user_token:
        token = x_user_token

    # 1. Direct mock token lookup
    if token and token in MOCK_USERS:
        return MOCK_USERS[token]

    # 2. Keycloak OIDC JWT payload parse
    if token:
        claims = decode_keycloak_jwt(token)
        if claims:
            user_id = claims.get("sub", "unknown")
            username = claims.get("preferred_username", claims.get("name", "keycloak_user"))
            roles_raw = claims.get("realm_access", {}).get("roles", [])
            user_roles = [Role(r) for r in roles_raw if r in Role.__members__.values()] or [Role.CITIZEN]
            
            # ABAC attributes from Keycloak user attributes / groups
            state_lgd = claims.get("state_lgd")
            district_lgd = claims.get("district_lgd")
            subdistrict_lgd = claims.get("subdistrict_lgd")
            allowed_wards = claims.get("allowed_wards", [])
            if isinstance(allowed_wards, str):
                allowed_wards = [w.strip() for w in allowed_wards.split(",")]

            return UserClaims(
                user_id=user_id,
                username=username,
                roles=user_roles,
                state_lgd=state_lgd,
                district_lgd=district_lgd,
                subdistrict_lgd=subdistrict_lgd,
                allowed_wards=allowed_wards,
                is_super="admin" in roles_raw or Role.SUPER_ADMIN in user_roles,
            )

    # 3. Dynamic Header-based role assignment (convenient for UI role-switcher demo)
    if x_user_role:
        role_enum = Role.CITIZEN
        try:
            role_enum = Role(x_user_role)
        except ValueError:
            pass
        wards = [x_user_ward] if x_user_ward else (["104", "105", "106"] if role_enum == Role.TAHSILDAR else [])
        return UserClaims(
            user_id=f"demo-{role_enum.value}",
            username=f"officer_{role_enum.value}",
            roles=[role_enum],
            allowed_wards=wards,
            is_super=role_enum in (Role.SUPER_ADMIN, Role.STATE_DIRECTOR),
        )

    # Default to public citizen with restricted scope
    return MOCK_USERS["token-citizen"]


def require_roles(*allowed_roles: Role) -> Callable[[UserClaims], UserClaims]:
    """RBAC Guard Dependency generator."""
    def role_checker(user: UserClaims = Depends(get_current_user)) -> UserClaims:
        if user.is_super or Role.SUPER_ADMIN in user.roles:
            return user
        if not any(role in user.roles for role in allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {[r.value for r in allowed_roles]}, user has: {[r.value for r in user.roles]}",
            )
        return user
    return role_checker
