"""
camp-service  (Camp Operations domain)  -- port 8002

POC scope only:
  * camp configuration (name, host city, location, dates, timezone, departments)
  * patient registration (FICTIONAL patients only)
  * consultation slot booking
  * publishes PatientRegistered and SlotBooked
  * consumes UserLoggedIn purely to show cross-domain event flow in the logs

Storage is a plain in-memory dict. Restarting the container clears everything.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
# 'date' is also a request field name below, so alias the type to avoid shadowing it
from datetime import date as DateType

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .eventbus import EventBus, utcnow_iso

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
logger = logging.getLogger("camp-service")

SERVICE_NAME = "camp-service"
SESSIONS = ["Morning", "Afternoon", "Evening"]

# ------------------------------------------------------- in-memory "database"
CAMPS: dict[str, dict] = {}
PATIENTS: dict[str, dict] = {}
SLOTS: dict[str, dict] = {}
LOGIN_ACTIVITY: list[dict] = []  # filled by the UserLoggedIn consumer

_counters = {"camp": 0, "patient": 0, "slot": 0}


def next_id(kind: str, prefix: str) -> str:
    _counters[kind] += 1
    return f"{prefix}-{_counters[kind]:04d}"


def seed_demo_camp() -> None:
    """One fictional camp so GET /camps/availability is never empty."""
    camp_id = next_id("camp", "CAMP")
    CAMPS[camp_id] = {
        "campId": camp_id,
        "campName": "Colombo Relief Medical Camp (fictional)",
        "hostCity": "Colombo",
        "location": "Community Hall, Main Street (fictional address)",
        "startDate": "2026-09-01",
        "endDate": "2026-09-05",
        "timezone": "Asia/Colombo",
        "departmentsOffered": ["General Medicine", "Dental", "Ophthalmology"],
        "createdAt": utcnow_iso(),
        "sampleData": True,
    }
    logger.info("[SEED] created fictional demo camp %s", camp_id)


bus = EventBus(SERVICE_NAME)


async def handle_user_logged_in(envelope: dict) -> None:
    """Demonstration consumer: camp-service just records who logged in."""
    data = envelope.get("data", {})
    LOGIN_ACTIVITY.append(
        {
            "userId": data.get("userId"),
            "displayName": data.get("displayName"),
            "roles": data.get("availableRoles"),
            "observedAt": utcnow_iso(),
        }
    )
    logger.info(
        "[FLOW] camp-service noted that %s (roles=%s) logged in -- no business action taken",
        data.get("displayName"),
        data.get("availableRoles"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_demo_camp()
    await bus.connect()
    await bus.consume(
        queue_name="camp-service.user-logged-in",
        routing_keys=["user.logged-in"],
        handler=handle_user_logged_in,
    )
    logger.info("[READY] %s listening on port %s", SERVICE_NAME, os.getenv("PORT", "8002"))
    yield
    await bus.close()


app = FastAPI(
    title="ITS RMS POC - camp-service",
    description=(
        "Camp Operations domain. Camp configuration, patient registration and slot booking. "
        "All patients and camps are FICTIONAL sample data."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ------------------------------------------------------------------ schemas
class CampCreate(BaseModel):
    campName: str = Field(..., examples=["Dubai Health Camp 2026 (fictional)"])
    hostCity: str = Field(..., examples=["Dubai"])
    location: str = Field(..., examples=["Markaz Hall, Sample Road (fictional)"])
    startDate: DateType = Field(..., examples=["2026-10-01"])
    endDate: DateType = Field(..., examples=["2026-10-04"])
    timezone: str = Field(default="Asia/Dubai", examples=["Asia/Dubai"])
    departmentsOffered: list[str] = Field(..., examples=[["General Medicine", "Dental"]])


class PatientRegister(BaseModel):
    patientName: str = Field(..., examples=["Zainab Demo (fictional)"])
    phone: str = Field(..., examples=["+919777777777"])
    campId: str = Field(..., examples=["CAMP-0001"])
    patientType: str = Field(..., examples=["ITS member"], description="'ITS member' or 'non-ITS member'")
    registeredBy: str | None = Field(default=None, examples=["usr-1001"], description="Data Entry / Admin user id")


class SlotBook(BaseModel):
    patientId: str = Field(..., examples=["PAT-0001"])
    campId: str = Field(..., examples=["CAMP-0001"])
    department: str = Field(..., examples=["General Medicine"])
    date: DateType = Field(..., examples=["2026-09-02"])
    session: str = Field(..., examples=["Morning"], description="Morning | Afternoon | Evening")


# ---------------------------------------------------------------- endpoints
@app.get("/health", tags=["ops"])
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "domain": "Camp Operations",
        "rabbitmqConnected": bus.connection is not None and not bus.connection.is_closed,
        "camps": len(CAMPS),
        "patients": len(PATIENTS),
        "slots": len(SLOTS),
        "time": utcnow_iso(),
    }


@app.get("/camps/availability", tags=["camps"], summary="Patient portal view of open camps")
async def camps_availability(city: str | None = Query(default=None, description="Optional host city filter")):
    """What a patient would see before registering: which camps are running where."""
    results = []
    for camp in CAMPS.values():
        if city and camp["hostCity"].lower() != city.lower():
            continue
        booked = sum(1 for s in SLOTS.values() if s["campId"] == camp["campId"])
        results.append(
            {
                "campId": camp["campId"],
                "campName": camp["campName"],
                "hostCity": camp["hostCity"],
                "location": camp["location"],
                "startDate": camp["startDate"],
                "endDate": camp["endDate"],
                "timezone": camp["timezone"],
                "departmentsOffered": camp["departmentsOffered"],
                "sessions": SESSIONS,
                "slotsBooked": booked,
                "status": "open",
            }
        )
    return {"disclaimer": "Fictional sample camps.", "count": len(results), "camps": results}


@app.get("/camps", tags=["camps"])
async def list_camps():
    return {"count": len(CAMPS), "camps": list(CAMPS.values())}


@app.post("/camps", status_code=201, tags=["camps"])
async def create_camp(payload: CampCreate):
    if payload.endDate < payload.startDate:
        raise HTTPException(status_code=400, detail="endDate cannot be before startDate")
    camp_id = next_id("camp", "CAMP")
    camp = {
        "campId": camp_id,
        "campName": payload.campName,
        "hostCity": payload.hostCity,
        "location": payload.location,
        "startDate": payload.startDate.isoformat(),
        "endDate": payload.endDate.isoformat(),
        "timezone": payload.timezone,
        "departmentsOffered": payload.departmentsOffered,
        "createdAt": utcnow_iso(),
        "sampleData": True,
    }
    CAMPS[camp_id] = camp
    logger.info("[CAMP] created %s '%s' in %s", camp_id, payload.campName, payload.hostCity)
    return camp


@app.post("/patients/register", status_code=201, tags=["patients"])
async def register_patient(payload: PatientRegister):
    """Publishes PatientRegistered (routing key: patient.registered)."""
    if payload.campId not in CAMPS:
        raise HTTPException(status_code=404, detail=f"Unknown campId '{payload.campId}'")
    if payload.patientType not in ("ITS member", "non-ITS member"):
        raise HTTPException(status_code=400, detail="patientType must be 'ITS member' or 'non-ITS member'")

    patient_id = next_id("patient", "PAT")
    patient = {
        "patientId": patient_id,
        "patientName": payload.patientName,
        "phone": payload.phone,
        "campId": payload.campId,
        "patientType": payload.patientType,
        "registeredBy": payload.registeredBy,
        "registeredAt": utcnow_iso(),
        "sampleData": True,
    }
    PATIENTS[patient_id] = patient
    logger.info("[PATIENT] registered %s '%s' for camp %s", patient_id, payload.patientName, payload.campId)

    await bus.publish(routing_key="patient.registered", event_type="PatientRegistered", data=patient)
    return patient


@app.get("/patients", tags=["patients"])
async def list_patients():
    return {"count": len(PATIENTS), "patients": list(PATIENTS.values())}


@app.post("/slots/book", status_code=201, tags=["slots"])
async def book_slot(payload: SlotBook):
    """Publishes SlotBooked (routing key: slot.booked)."""
    camp = CAMPS.get(payload.campId)
    if camp is None:
        raise HTTPException(status_code=404, detail=f"Unknown campId '{payload.campId}'")
    if payload.patientId not in PATIENTS:
        raise HTTPException(status_code=404, detail=f"Unknown patientId '{payload.patientId}'")
    if payload.department not in camp["departmentsOffered"]:
        raise HTTPException(
            status_code=400,
            detail=f"Camp {camp['campId']} does not offer '{payload.department}'. Offered: {camp['departmentsOffered']}",
        )
    if payload.session not in SESSIONS:
        raise HTTPException(status_code=400, detail=f"session must be one of {SESSIONS}")

    slot_id = next_id("slot", "SLOT")
    slot = {
        "slotId": slot_id,
        "patientId": payload.patientId,
        "campId": payload.campId,
        "department": payload.department,
        "date": payload.date.isoformat(),
        "session": payload.session,
        "bookedAt": utcnow_iso(),
    }
    SLOTS[slot_id] = slot
    logger.info(
        "[SLOT] booked %s for patient %s (%s, %s %s)",
        slot_id, payload.patientId, payload.department, payload.date, payload.session,
    )

    await bus.publish(routing_key="slot.booked", event_type="SlotBooked", data=slot)
    return slot


@app.get("/slots", tags=["slots"])
async def list_slots():
    return {"count": len(SLOTS), "slots": list(SLOTS.values())}


@app.get("/login-activity", tags=["ops"], summary="Proof that UserLoggedIn was consumed")
async def login_activity():
    return {"count": len(LOGIN_ACTIVITY), "activity": LOGIN_ACTIVITY}
