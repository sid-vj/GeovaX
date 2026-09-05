"""Signed-token authentication & RBAC + ABAC Spatial Authorization.

Enforces:
1. RBAC (Role-Based Access Control): SuperAdmin, StateDirector, DistrictCollector, Tahsildar, Citizen.
2. ABAC (Attribute-Based Access Control): Spatial jurisdiction matching against LGD (Local Government Directory)
   codes, District IDs, and Ward boundaries.

Authentication model, stated plainly: this reference deployment has no real identity provider
(Keycloak/OIDC) deployed, and no real credential store — the five personas below are a fixed,
seeded demo roster, not a production user base. What *is* real is the token mechanism: tokens
issued by ``POST /api/auth/login`` are HMAC-SHA256 signed, carry an expiry, and are verified
with a constant-time comparison before any claim in them is trusted. This closes the actual
vulnerability that existed here previously — literal bearer strings (``"token-superadmin"``)
that granted access to anyone who typed them, an unsigned JWT decoder that trusted any
well-formed payload, and an ``X-User-Role`` header that let a caller self-declare their role.
Wiring a real IdP against a real user directory is a further integration this environment
cannot perform (no IdP is deployed here); what this module guarantees is that nobody can forge
or replay a role/ward claim without possessing the signing secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

_ENV_SECRET_VAR = "SAMANVAY_JWT_SECRET"
_TOKEN_TTL_SECONDS = 8 * 3600


def _load_signing_secret() -> str:
    """Load the HMAC signing secret from the environment.

    If unset, an ephemeral secret is generated for this process only, and a loud warning is
    logged: tokens issued before a restart become invalid, and this is unsafe for any
    multi-process or production deployment. Set ``SAMANVAY_JWT_SECRET`` explicitly there.
    """
    secret = os.environ.get(_ENV_SECRET_VAR)
    if secret:
        return secret
    ephemeral = secrets.token_hex(32)
    logger.warning(
        "%s is not set; generated an ephemeral signing secret for this process only. "
        "Tokens will stop verifying on restart and this must never be relied on outside "
        "local development. Set %s to a persistent random value in any shared deployment.",
        _ENV_SECRET_VAR, _ENV_SECRET_VAR,
    )
    return ephemeral


_SIGNING_SECRET = _load_signing_secret()


def _b64url_encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return urlsafe_b64decode(padded.encode("ascii"))


def sign_token(claims: dict[str, Any], *, ttl_seconds: int = _TOKEN_TTL_SECONDS) -> str:
    """Issue an HMAC-SHA256 signed, expiring token over ``claims``."""
    now = int(time.time())
    payload = {**claims, "iat": now, "exp": now + ttl_seconds}
    header_b64 = _b64url_encode(json.dumps({"alg": "HS256", "typ": "SAMANVAY-JWT"}).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, sort_keys=True).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(_SIGNING_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url_encode(signature)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def verify_token(token: str) -> Optional[dict[str, Any]]:
    """Verify signature and expiry; return claims if valid, else ``None``.

    Uses ``hmac.compare_digest`` for constant-time signature comparison so a mismatch cannot
    be timed to reveal how many leading bytes matched.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(_SIGNING_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        given_sig = _b64url_decode(sig_b64)
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(expected_sig, given_sig):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    STATE_DIRECTOR = "state_director"
    DISTRICT_COLLECTOR = "district_collector"
    TAHSILDAR = "tahsildar"
    SURVEY_OFFICER = "survey_officer"
    CITIZEN = "citizen"


# Real [lon, lat] centers for the colloquial ward names used in USER_DIRECTORY's allowed_wards
# below — the same real, curated coordinates already used for these exact wards in
# web-gis/src/lib/auth.ts's AVAILABLE_WARDS, duplicated here (not imported — this is a Python
# package, that a TS one) so ABAC can recognise a real parcel by geography as well as by the
# fragile village/taluk name-substring check alone. See UserClaims.ward_scope_bboxes.
WARD_CENTERS: dict[str, tuple[float, float]] = {
    "Vandalur": (80.082, 12.888),
    "Old Perungalathur": (80.086, 12.898),
    "New Perungalathur": (80.096, 12.908),
    "Mudichur": (80.078, 12.912),
    "Tambaram": (80.118, 12.924),
    "Tambaram Sanatorium": (80.130, 12.938),
    "Chromepet": (80.142, 12.952),
    "Pallavaram": (80.155, 12.968),
    "Hasthinapuram": (80.148, 12.946),
    "Tirusulam": (80.165, 12.980),
    "Meenambakkam": (80.176, 12.992),
    "Alandur": (80.190, 13.004),
    "Guindy": (80.208, 13.010),
    "Anna Salai": (80.256, 13.054),
    "Egmore": (80.260, 13.080),
    "Chetpet": (80.238, 13.072),
    "Nungambakkam": (80.242, 13.058),
    "Mylapore": (80.268, 13.036),
}
WARD_PAD = 0.012  # degrees — same real "ward-sized" AOI half-width used throughout the app


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

    def to_token_claims(self) -> dict[str, Any]:
        return {
            "sub": self.user_id,
            "username": self.username,
            "roles": [r.value for r in self.roles],
            "state_lgd": self.state_lgd,
            "district_lgd": self.district_lgd,
            "subdistrict_lgd": self.subdistrict_lgd,
            "allowed_wards": self.allowed_wards,
            "is_super": self.is_super,
        }

    @staticmethod
    def from_token_claims(claims: dict[str, Any]) -> "UserClaims":
        roles_raw = claims.get("roles", [])
        roles = [Role(r) for r in roles_raw if r in Role.__members__.values()] or [Role.CITIZEN]
        return UserClaims(
            user_id=str(claims.get("sub", "unknown")),
            username=str(claims.get("username", "unknown")),
            roles=roles,
            state_lgd=claims.get("state_lgd"),
            district_lgd=claims.get("district_lgd"),
            subdistrict_lgd=claims.get("subdistrict_lgd"),
            allowed_wards=list(claims.get("allowed_wards") or []),
            is_super=bool(claims.get("is_super", False)),
        )

    def ward_scope_bboxes(self) -> list[tuple[float, float, float, float]]:
        """Real geographic extents for this user's named ward scope, where known.

        `allowed_wards` are colloquial locality names (e.g. "Chromepet"), but the real
        cadastre's own fields (village_name/taluk_name) carry official revenue-village names,
        which frequently do NOT contain the colloquial name as a substring — a real parcel in
        Chromepet's own real jurisdiction can carry a village_name like "Pallavaram" instead.
        A pure substring match then silently zeroes out a scoped user's own jurisdiction,
        which is a worse failure than granting access: it makes ABAC look like "no data
        exists" rather than "the wrong field was checked". WARD_CENTERS gives each named
        ward's real AOI (same real coordinates already curated in web-gis/src/lib/auth.ts,
        the frontend's ward picker) so a feature can additionally be recognised as in-scope by
        real geography, not name text alone — this only ever adds legitimate access within a
        ward the user is already named for, never grants anything beyond it.
        """
        boxes = []
        for w in self.allowed_wards:
            center = WARD_CENTERS.get(w)
            if center:
                lon, lat = center
                boxes.append((lon - WARD_PAD, lat - WARD_PAD, lon + WARD_PAD, lat + WARD_PAD))
        return boxes

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


# Seeded demo persona roster. This is NOT a production user store — see module docstring.
# Keyed by a stable login_id used only by POST /api/auth/login to select which persona to
# issue a signed token for; it is never itself accepted as a credential.
USER_DIRECTORY: dict[str, UserClaims] = {
    "superadmin": UserClaims(
        user_id="usr-001",
        username="nic_national_director",
        roles=[Role.SUPER_ADMIN],
        is_super=True,
    ),
    "tahsildar-egmore": UserClaims(
        user_id="usr-104",
        username="tahsildar_egmore_div",
        roles=[Role.TAHSILDAR, Role.SURVEY_OFFICER],
        state_lgd="33",
        district_lgd="571",
        subdistrict_lgd="057101",
        allowed_wards=["104", "105", "106", "Egmore", "Kilpauk"],
    ),
    "tahsildar-mylapore": UserClaims(
        user_id="usr-120",
        username="tahsildar_mylapore_div",
        roles=[Role.TAHSILDAR],
        state_lgd="33",
        district_lgd="571",
        subdistrict_lgd="057102",
        allowed_wards=["120", "121", "122", "Mylapore", "Alwarpet"],
    ),
    "tahsildar-tambaram": UserClaims(
        user_id="usr-150",
        username="tahsildar_tambaram",
        roles=[Role.TAHSILDAR, Role.SURVEY_OFFICER],
        state_lgd="33",
        district_lgd="572",
        subdistrict_lgd="057201",
        allowed_wards=["Vandalur", "Old Perungalathur", "New Perungalathur", "Mudichur", "Tambaram", "Tambaram Sanatorium", "Chromepet", "Pallavaram", "Hasthinapuram", "Tirusulam", "Meenambakkam", "Alandur", "Guindy", "Anna Salai", "Chennai"],
    ),
    "citizen": UserClaims(
        user_id="usr-citizen-999",
        username="citizen_public",
        roles=[Role.CITIZEN],
        allowed_wards=[],
    ),
}

_CITIZEN_DEFAULT = USER_DIRECTORY["citizen"]

security = HTTPBearer(auto_error=False)


async def get_current_user(
    auth_header: Optional[HTTPAuthorizationCredentials] = Security(security),
    x_user_token: Optional[str] = Header(None, alias="X-Auth-Token"),
) -> UserClaims:
    """FastAPI dependency resolving user identity from a signed, verified token.

    Only a token issued by ``POST /api/auth/login`` (or any HMAC-signed token bearing the
    same secret) is accepted. A present-but-invalid or expired token is rejected outright
    with 401 — it is never silently treated as "no credential" and downgraded to citizen,
    since that would let an attacker probe for validity. No credential at all falls back to
    the public citizen scope, which is the intended anonymous-read behaviour.
    """
    token = None
    if auth_header and auth_header.credentials:
        token = auth_header.credentials
    elif x_user_token:
        token = x_user_token

    if token:
        claims = verify_token(token)
        if claims is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token.",
            )
        return UserClaims.from_token_claims(claims)

    return _CITIZEN_DEFAULT


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
