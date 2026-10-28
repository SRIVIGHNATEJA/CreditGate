import os
import sys
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.policy import Verdict

APP_PATH = str(BASE_DIR / "app.py")


def test_app_headless_mounts_cleanly():
    """
    Verifies that app.py initializes cleanly in headless mode without exceptions,
    all 4 navigation pages mount cleanly, and core session state keys are populated.
    """
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run(timeout=30)

    # 1. Zero unhandled exceptions or tracebacks on default page (Case)
    assert len(at.exception) == 0, f"AppTest encountered unhandled exceptions: {[e.message for e in at.exception]}"

    # 2. Check core session state initializations
    assert "app_id" in at.session_state
    assert at.session_state.app_id.startswith("APP-")
    assert "audit_trail" in at.session_state
    assert isinstance(at.session_state.audit_trail, list)
    assert "research_cache" in at.session_state
    assert isinstance(at.session_state.research_cache, dict)
    assert "scored_composite_hash" in at.session_state

    # 3. Verify each navigation page mounts cleanly without exceptions
    for page_path in ["app_pages/evidence.py", "app_pages/decision.py", "app_pages/cam_audit.py"]:
        at.switch_page(page_path).run(timeout=30)
        assert len(at.exception) == 0, f"Page {page_path} failed to mount cleanly: {[e.message for e in at.exception]}"


def test_app_cohort1_end_to_end_flow():
    """
    Verifies the full click-path through UI:
    Cohort selection -> Multimodal extraction -> Financial locking -> Five Cs Risk Engine execution.
    """
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run(timeout=30)

    # Select Cohort 1
    next(b for b in at.button if b.key == "btn_open_prime").click().run(timeout=30)
    assert len(at.exception) == 0

    # Switch to Evidence page and run extraction
    at.switch_page("app_pages/evidence.py").run(timeout=30)
    assert len(at.exception) == 0

    for btn in at.button:
        if "Extract" in btn.label:
            btn.click().run(timeout=30)
            break
    assert len(at.exception) == 0

    # Click Confirm & Lock on Evidence page
    for btn in at.button:
        if "Confirm & Lock" in btn.label:
            btn.click().run(timeout=30)
            break
    assert len(at.exception) == 0
    assert at.session_state.hitl_confirmed is True

    # Switch to Decision page and execute Risk Engine
    at.switch_page("app_pages/decision.py").run(timeout=30)
    assert len(at.exception) == 0

    for btn in at.button:
        if "Run Deterministic" in btn.label:
            btn.click().run(timeout=30)
            break
    assert len(at.exception) == 0
    assert at.session_state.score_result is not None
    assert at.session_state.score_result.final_verdict == Verdict.APPROVE

    # Switch to CAM & Audit page with active score_result and verify clean render
    at.switch_page("app_pages/cam_audit.py").run(timeout=30)
    assert len(at.exception) == 0, f"CAM & Audit page raised exception: {[e.message for e in at.exception]}"
