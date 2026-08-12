"""
identity-service  (Identity & Access domain)  -- port 8001

POC scope only:
  * fictional, hard-coded users (NO database, NO real authentication)
  * one phone number may map to several roles
  * returns a fake session token so the other services have something to echo
  * publishes UserLoggedIn on the its.rms.events topic exchange

NOT production auth. There is no password, no OTP verification, no JWT signing.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .eventbus import EventBus, utcnow_iso

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
logger = logging.getLogger("identity-service")

SERVICE_NAME = "identity-service"

# --------------------------------------------------------------------------
# FICTIONAL USER DIRECTORY - every person below is made up for this demo.
# In the real ITS RMS this would come from ITS + the RMS user tables.
# --------------------------------------------------------------------------
FICTIONAL_USERS: dict[str, dict] = {
    "+919999999999": {
        "userId": "usr-1001",
        "displayName": "Fatema Demo (fictional)",
        "roles": ["Admin", "Data Entry"],  # <-- the multi-role demo user
    },
    "+919000000000": {
        "userId": "usr-1000",
        "displayName": "Root Demo (fictional)",
        "roles": ["Super Admin"],
    },
    "+919888888888": {
        "userId": "usr-1002",
        "displayName": "Dr. Huzefa Demo (fictional)",
        "roles": ["Doctor"],
    },
    "+919777777777": {
        "userId": "usr-1003",
        "displayName": "Zainab Demo (fictional)",
        "roles": ["Patient"],
    },
}

ALL_ROLES = ["Super Admin", "Admin", "Data Entry", "Doctor", "Patient"]

# Fake in-memory session store. Lost on restart - that is fine for a POC.
SESSIONS: dict[str, dict] = {}

bus = EventBus(SERVICE_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bus.connect()
    logger.info("[READY] %s listening on port %s", SERVICE_NAME, os.getenv("PORT", "8001"))
    yield
    await bus.close()


app = FastAPI(
    title="ITS RMS POC - identity-service",
    description=(
        "Identity & Access domain. Combined login + role selection for the medical camp POC. "
        "All users are FICTIONAL and authentication is FAKE."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ------------------------------------------------------------------ schemas
class LoginRequest(BaseModel):
    phone: str = Field(..., examples=["+919999999999"], description="Fictional demo phone number")


class LoginResponse(BaseModel):
    sessionToken: str
    userId: str
    displayName: str
    phone: str
    availableRoles: list[str]
    requiresRoleSelection: bool
    note: str


class SelectRoleRequest(BaseModel):
    sessionToken: str
    role: str = Field(..., examples=["Data Entry"])


class SelectRoleResponse(BaseModel):
    sessionToken: str
    userId: str
    activeRole: str
    availableRoles: list[str]


# ---------------------------------------------------------------- endpoints
@app.get("/health", tags=["ops"])
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "domain": "Identity & Access",
        "rabbitmqConnected": bus.connection is not None and not bus.connection.is_closed,
        "time": utcnow_iso(),
    }


@app.get("/demo-users", tags=["identity"], summary="List the fictional demo logins")
async def demo_users():
    return {
        "disclaimer": "All users are fictional sample data created for this POC.",
        "roles": ALL_ROLES,
        "users": [
            {"phone": phone, "displayName": u["displayName"], "roles": u["roles"]}
            for phone, u in FICTIONAL_USERS.items()
        ],
    }


@app.post("/login", response_model=LoginResponse, tags=["identity"])
async def login(payload: LoginRequest):
    """Fake login. Publishes UserLoggedIn (routing key: user.logged-in)."""
    user = FICTIONAL_USERS.get(payload.phone.strip())
    if user is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown demo phone '{payload.phone}'. Try GET /demo-users for the fictional list.",
        )

    token = f"fake-session-{uuid.uuid4().hex[:12]}"
    SESSIONS[token] = {
        "userId": user["userId"],
        "phone": payload.phone,
        "displayName": user["displayName"],
        "availableRoles": user["roles"],
        "activeRole": user["roles"][0] if len(user["roles"]) == 1 else None,
        "issuedAt": utcnow_iso(),
    }
    logger.info("[LOGIN] %s (%s) roles=%s", user["displayName"], payload.phone, user["roles"])

    await bus.publish(
        routing_key="user.logged-in",
        event_type="UserLoggedIn",
        data={
            "userId": user["userId"],
            "phone": payload.phone,
            "displayName": user["displayName"],
            "availableRoles": user["roles"],
            "sessionToken": token,
            "loggedInAt": utcnow_iso(),
        },
    )

    return LoginResponse(
        sessionToken=token,
        userId=user["userId"],
        displayName=user["displayName"],
        phone=payload.phone,
        availableRoles=user["roles"],
        requiresRoleSelection=len(user["roles"]) > 1,
        note="Fictional user. No password or OTP is checked in this POC.",
    )


@app.post("/select-role", response_model=SelectRoleResponse, tags=["identity"])
async def select_role(payload: SelectRoleRequest):
    """A phone number can hold several roles, so the user picks one per session."""
    session = SESSIONS.get(payload.sessionToken)
    if session is None:
        raise HTTPException(status_code=401, detail="Unknown session token. Call POST /login first.")
    if payload.role not in session["availableRoles"]:
        raise HTTPException(
            status_code=403,
            detail=f"Role '{payload.role}' not available for this user. Available: {session['availableRoles']}",
        )

    session["activeRole"] = payload.role
    logger.info("[ROLE] %s selected role '%s'", session["displayName"], payload.role)
    return SelectRoleResponse(
        sessionToken=payload.sessionToken,
        userId=session["userId"],
        activeRole=payload.role,
        availableRoles=session["availableRoles"],
    )


@app.get("/sessions/{session_token}", tags=["identity"])
async def get_session(session_token: str):
    session = SESSIONS.get(session_token)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session token")
    return session
