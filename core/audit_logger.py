import os
import re
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

IST = ZoneInfo("Asia/Kolkata")

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = BASE_DIR / "outputs"


def mask_identifier(val: str, id_type: str = "PAN") -> str:
    """Masks sensitive PII before persistence in append-only audit event streams."""
    if not val:
        return "N/A"
    clean = str(val).strip()
    if id_type.upper() == "PAN" and len(clean) == 10:
        return f"{clean[:2]}***{clean[-2:]}"
    elif id_type.upper() == "AADHAAR":
        return "XXXX-XXXX-XXXX"
    elif id_type.upper() in ["ACCOUNT", "BANK"]:
        return f"****{clean[-4:]}" if len(clean) >= 4 else "****"
    elif len(clean) > 6:
        return f"{clean[:3]}...{clean[-3:]}"
    return "***"


def sanitize_audit_payload(details: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively traverses audit details dict and masks any sensitive keys."""
    sanitized = {}
    for k, v in details.items():
        k_lower = k.lower()
        if "pan" in k_lower and isinstance(v, str):
            sanitized[k] = mask_identifier(v, "PAN")
        elif "aadhaar" in k_lower and isinstance(v, str):
            sanitized[k] = mask_identifier(v, "AADHAAR")
        elif "account" in k_lower and isinstance(v, str):
            sanitized[k] = mask_identifier(v, "ACCOUNT")
        elif isinstance(v, dict):
            sanitized[k] = sanitize_audit_payload(v)
        elif isinstance(v, list):
            sanitized[k] = [
                sanitize_audit_payload(item) if isinstance(item, dict) else item
                for item in v
            ]
        else:
            sanitized[k] = str(v) if hasattr(v, "__str__") and not isinstance(v, (int, float, bool, type(None))) else v
    return sanitized


import hashlib


def compute_event_hash(event_data: Dict[str, Any]) -> str:
    """Computes SHA-256 hash over canonical JSON representation of event fields."""
    payload = {k: v for k, v in event_data.items() if k != "event_hash"}
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


class AuditLogger:
    """
    Tamper-evident append-only event sourcing logger for MSME credit underwriting.
    Maintains a SHA-256 hash chain across chronological events.
    Notice: This is tamper-evident (detects modifications), not tamper-proof.
    """

    def __init__(self, app_id: str, log_dir: Optional[Path] = None):
        self.app_id = app_id
        target_dir = log_dir or OUTPUTS_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = target_dir / f"audit_trail_{self.app_id}.jsonl"
        self.log_path = self.log_file

    def log(
        self,
        event_type: str,
        actor: str,
        details: Dict[str, Any],
        severity: str = "INFO"
    ) -> Dict[str, Any]:
        """Appends a tamper-evident audit event bound to the previous event hash."""
        sanitized_details = sanitize_audit_payload(details)
        prev_events = self.load_events()
        prev_hash = prev_events[-1].get("event_hash", "GENESIS") if prev_events else "GENESIS"

        event = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "app_id": self.app_id,
            "event_type": event_type,
            "actor": actor,
            "severity": severity,
            "details": sanitized_details,
            "prev_hash": prev_hash
        }
        event["event_hash"] = compute_event_hash(event)

        # Thread-safe append to JSONL disk file
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            # Audit failures must not crash the primary underwriting engine
            print(f"[AUDIT LOGGER ERROR] Failed to write to {self.log_file}: {e}")

        return event

    def load_events(self) -> List[Dict[str, Any]]:
        """Reads all events for this application from the append-only stream."""
        if not self.log_file.exists():
            return []
        events = []
        with open(self.log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return events

    def verify_chain(self, events: Optional[List[Dict[str, Any]]] = None) -> Tuple[bool, Optional[str]]:
        """Verifies the tamper-evident SHA-256 hash chain across chronological events."""
        return verify_chain(events if events is not None else self.load_events())


def verify_chain(target: Any) -> Tuple[bool, Optional[str]]:
    """
    Verifies the tamper-evident SHA-256 hash chain across chronological events.
    Accepts a file path (str or Path) or a list of event dictionaries.
    Returns (is_valid, error_reason).
    """
    if isinstance(target, (str, Path)):
        p = Path(target)
        if not p.exists():
            return False, f"Audit log file not found: {p}"
        events = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        return False, f"Corrupted JSON line: {e}"
        evts = events
    elif isinstance(target, list):
        evts = target
    else:
        return False, f"Invalid target type for verify_chain: {type(target)}"

    if not evts:
        return True, None

    for idx, evt in enumerate(evts):
        if "event_hash" not in evt:
            return False, f"Event at index {idx} missing event_hash"

        stored_hash = evt["event_hash"]
        expected_hash = compute_event_hash(evt)
        if stored_hash != expected_hash:
            return False, f"Hash mismatch at sequence {idx}: payload tampered (stored={stored_hash}, computed={expected_hash})"

        expected_prev = "GENESIS" if idx == 0 else evts[idx - 1].get("event_hash")
        actual_prev = evt.get("prev_hash")
        if actual_prev != expected_prev:
            return False, f"Broken chain link at sequence {idx}: prev_hash={actual_prev}, expected={expected_prev}"

    return True, None


def log_event(
    app_id: str,
    event_type: str,
    actor: str,
    details: Dict[str, Any],
    severity: str = "INFO"
) -> Dict[str, Any]:
    """Functional helper for lightweight audit logging without maintaining instance state."""
    logger = AuditLogger(app_id)
    return logger.log(event_type=event_type, actor=actor, details=details, severity=severity)
