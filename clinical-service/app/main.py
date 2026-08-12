"""
clinical-service  (Clinical Care domain)  -- port 8003

POC scope only:
  * consumes PatientRegistered  -> opens a clinical case
  * consumes SlotBooked         -> attaches the consultation slot to that case
  * doctor records vitals       -> publishes VitalsRecorded
  * doctor records a diagnosis  -> stored on the case (no event in this POC)
  * doctor closes the case      -> publishes CaseClosed

All clinical data here is FICTIONAL. Nothing is medical advice and nothing is
persisted beyond the life of the container (plain in-memory dict).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import date as DateType

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .eventbus import EventBus, utcnow_iso

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
logger = logging.getLogger("clinical-service")

SERVICE_NAME = "clinical-service"

# ------------------------------------------------------- in-memory "database"
CASES: dict[str, dict] = {}
_case_counter = {"n": 0}
# Slot events can technically arrive before the patient event; park them here.
ORPHAN_SLOTS: list[dict] = []


def next_case_id() -> str:
    _case_counter["n"] += 1
    return f"CASE-{_case_counter['n']:04d}"


def find_case_by_patient(patient_id: str) -> dict | None:
    for case in CASES.values():
        if case["patientId"] == patient_id:
            return case
    return None


bus = EventBus(SERVICE_NAME)


# ------------------------------------------------------------------ consumers
async def handle_patient_registered(envelope: dict) -> None:
    data = envelope.get("data", {})
    patient_id = data.get("patientId")
    if find_case_by_patient(patient_id):
        logger.info("[FLOW] case already open for patient %s, ignoring duplicate event", patient_id)
        return

    case_id = next_case_id()
    CASES[case_id] = {
        "caseId": case_id,
        "patientId": patient_id,
        "patientName": data.get("patientName"),
        "patientType": data.get("patientType"),
        "campId": data.get("campId"),
        "status": "open",
        "slot": None,
        "vitals": None,
        "diagnosis": None,
        "openedAt": utcnow_iso(),
        "closedAt": None,
        "sampleData": True,
    }
    logger.info(
        "[FLOW] PatientRegistered -> opened clinical case %s for patient %s (%s)",
        case_id, patient_id, data.get("patientName"),
    )

    # Attach any slot that arrived before this patient's case existed.
    for slot in list(ORPHAN_SLOTS):
        if slot.get("patientId") == patient_id:
            CASES[case_id]["slot"] = slot
            ORPHAN_SLOTS.remove(slot)
            logger.info("[FLOW] attached previously-orphaned slot %s to case %s", slot.get("slotId"), case_id)


async def handle_slot_booked(envelope: dict) -> None:
    data = envelope.get("data", {})
    patient_id = data.get("patientId")
    case = find_case_by_patient(patient_id)
    if case is None:
        ORPHAN_SLOTS.append(data)
        logger.warning(
            "[FLOW] SlotBooked for patient %s but no case yet -- parked until PatientRegistered arrives",
            patient_id,
        )
        return
    case["slot"] = data
    logger.info(
        "[FLOW] SlotBooked -> case %s assigned %s on %s (%s)",
        case["caseId"], data.get("department"), data.get("date"), data.get("session"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bus.connect()
    await bus.consume(
        queue_name="clinical-service.patient-registered",
        routing_keys=["patient.registered"],
        handler=handle_patient_registered,
    )
    await bus.consume(
        queue_name="clinical-service.slot-booked",
        routing_keys=["slot.booked"],
        handler=handle_slot_booked,
    )
    logger.info("[READY] %s listening on port %s", SERVICE_NAME, os.getenv("PORT", "8003"))
    yield
    await bus.close()


app = FastAPI(
    title="ITS RMS POC - clinical-service",
    description=(
        "Clinical Care domain. Doctor consultation, vitals, diagnosis and case closure. "
        "All clinical data is FICTIONAL sample data."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ------------------------------------------------------------------ schemas
class Vitals(BaseModel):
    temperature: float = Field(..., examples=[37.2], description="Degrees Celsius")
    bloodPressure: str = Field(..., examples=["120/80"])
    weight: float = Field(..., examples=[68.5], description="Kilograms")
    notes: str | None = Field(default=None, examples=["Patient reports mild headache (fictional)"])
    recordedBy: str | None = Field(default=None, examples=["usr-1002"])


class Diagnosis(BaseModel):
    chiefComplaint: str = Field(..., examples=["Headache and fatigue for 3 days (fictional)"])
    diagnosis: str = Field(..., examples=["Tension headache (fictional)"])
    medication: str = Field(..., examples=["Paracetamol 500mg, twice daily (fictional)"])
    followUpDate: DateType | None = Field(default=None, examples=["2026-09-15"])
    recordedBy: str | None = Field(default=None, examples=["usr-1002"])


class CloseCase(BaseModel):
    closedBy: str | None = Field(default=None, examples=["usr-1002"])
    closingNotes: str | None = Field(default=None, examples=["Consultation complete (fictional)"])


def get_case_or_404(case_id: str) -> dict:
    case = CASES.get(case_id)
    if case is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown caseId '{case_id}'. Cases are created automatically when a "
                "PatientRegistered event arrives from camp-service. Try GET /cases."
            ),
        )
    return case


# ---------------------------------------------------------------- endpoints
@app.get("/health", tags=["ops"])
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "domain": "Clinical Care",
        "rabbitmqConnected": bus.connection is not None and not bus.connection.is_closed,
        "cases": len(CASES),
        "orphanSlots": len(ORPHAN_SLOTS),
        "time": utcnow_iso(),
    }


@app.get("/cases", tags=["cases"], summary="List cases created from PatientRegistered events")
async def list_cases(patient_id: str | None = Query(default=None, description="Filter by patientId")):
    cases = list(CASES.values())
    if patient_id:
        cases = [c for c in cases if c["patientId"] == patient_id]
    return {"disclaimer": "Fictional sample cases.", "count": len(cases), "cases": cases}


@app.get("/cases/{case_id}", tags=["cases"])
async def get_case(case_id: str):
    return get_case_or_404(case_id)


@app.post("/cases/{case_id}/vitals", tags=["cases"])
async def record_vitals(case_id: str, payload: Vitals):
    """Publishes VitalsRecorded (routing key: vitals.recorded)."""
    case = get_case_or_404(case_id)
    if case["status"] == "closed":
        raise HTTPException(status_code=409, detail=f"Case {case_id} is already closed")

    vitals = {
        "temperature": payload.temperature,
        "bloodPressure": payload.bloodPressure,
        "weight": payload.weight,
        "notes": payload.notes,
        "recordedBy": payload.recordedBy,
        "recordedAt": utcnow_iso(),
    }
    case["vitals"] = vitals
    case["status"] = "in-consultation"
    logger.info("[CASE] vitals recorded on %s: %s", case_id, vitals)

    await bus.publish(
        routing_key="vitals.recorded",
        event_type="VitalsRecorded",
        data={"caseId": case_id, "patientId": case["patientId"], **vitals},
    )
    return case


@app.post("/cases/{case_id}/diagnosis", tags=["cases"])
async def record_diagnosis(case_id: str, payload: Diagnosis):
    """Stored on the case. This POC does not publish a separate diagnosis event."""
    case = get_case_or_404(case_id)
    if case["status"] == "closed":
        raise HTTPException(status_code=409, detail=f"Case {case_id} is already closed")

    diagnosis = {
        "chiefComplaint": payload.chiefComplaint,
        "diagnosis": payload.diagnosis,
        "medication": payload.medication,
        "followUpDate": payload.followUpDate.isoformat() if payload.followUpDate else None,
        "recordedBy": payload.recordedBy,
        "recordedAt": utcnow_iso(),
    }
    case["diagnosis"] = diagnosis
    case["status"] = "in-consultation"
    logger.info("[CASE] diagnosis recorded on %s: %s", case_id, diagnosis["diagnosis"])
    return case


@app.post("/cases/{case_id}/close", tags=["cases"])
async def close_case(case_id: str, payload: CloseCase | None = None):
    """Publishes CaseClosed (routing key: case.closed)."""
    case = get_case_or_404(case_id)
    if case["status"] == "closed":
        raise HTTPException(status_code=409, detail=f"Case {case_id} is already closed")

    payload = payload or CloseCase()
    case["status"] = "closed"
    case["closedAt"] = utcnow_iso()
    case["closedBy"] = payload.closedBy
    case["closingNotes"] = payload.closingNotes
    diagnosis = case.get("diagnosis") or {}
    logger.info("[CASE] closed %s for patient %s", case_id, case["patientId"])

    await bus.publish(
        routing_key="case.closed",
        event_type="CaseClosed",
        data={
            "caseId": case_id,
            "patientId": case["patientId"],
            "campId": case["campId"],
            "chiefComplaint": diagnosis.get("chiefComplaint"),
            "diagnosis": diagnosis.get("diagnosis"),
            "medication": diagnosis.get("medication"),
            "followUpDate": diagnosis.get("followUpDate"),
            "closedBy": payload.closedBy,
            "closedAt": case["closedAt"],
        },
    )
    return case
