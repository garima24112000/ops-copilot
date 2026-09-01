"""A mock internal ITSM/ops API. Stands in for the ticketing system and the service-restart
endpoint a real internal IT team would have. State is kept in a local JSON file so a demo run
can show a ticket actually "existing" afterward.

Deliberately fake and deliberately simple: this project's contribution is the retrieval +
agent + observability loop around tool calls, not a ticketing system.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

STATE_PATH = Path(__file__).resolve().parent / "mock_api_state.json"


def _load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        loaded: dict[str, Any] = json.loads(STATE_PATH.read_text())
        return loaded
    return {"tickets": [], "restarts": []}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def create_ticket(title: str, body: str, severity: str) -> dict[str, Any]:
    state = _load_state()
    ticket = {
        "ticket_id": f"OPS-{uuid.uuid4().hex[:8].upper()}",
        "title": title,
        "body": body,
        "severity": severity,
        "status": "open",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }
    state["tickets"].append(ticket)
    _save_state(state)
    return ticket


def restart_service(service: str) -> dict[str, Any]:
    state = _load_state()
    record = {
        "restart_id": f"RESTART-{uuid.uuid4().hex[:8].upper()}",
        "service": service,
        "status": "restarted",
        "at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }
    state["restarts"].append(record)
    _save_state(state)
    return record
