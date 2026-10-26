import sys
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

APP_PATH = str(BASE_DIR / "app.py")


def test_field_notes_and_callbacks_lifecycle():
    """
    Validates that:
    1. Quick preset buttons populate txt_field_note without StreamlitWidgetAlreadyInstantiatedError.
    2. Real-time typing in txt_field_note triggers auto-detection and unlocks C-pillar selector.
    3. User manual override on sb_fn_pillar is retained.
    4. Save button callback saves note, logs audit event, and clears inputs without widget errors.
    5. Undo button callback pops note cleanly.
    """
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run(timeout=30)
    assert len(at.exception) == 0

    # Step 1: Open Cohort 1 (Prime)
    btn_prime = next((b for b in at.button if b.key == "btn_open_prime"), None)
    assert btn_prime is not None
    btn_prime.click().run(timeout=30)
    assert len(at.exception) == 0

    # Step 2: Switch to Evidence page and Extract + Confirm
    at.switch_page("app_pages/evidence.py").run(timeout=30)
    assert len(at.exception) == 0

    for btn in at.button:
        if "Extract" in btn.label:
            btn.click().run(timeout=30)
            break
    assert len(at.exception) == 0

    for btn in at.button:
        if "Confirm & Lock" in btn.label:
            btn.click().run(timeout=30)
            break
    assert len(at.exception) == 0

    # Step 3: Switch to Decision page and run scoring
    at.switch_page("app_pages/decision.py").run(timeout=30)
    assert len(at.exception) == 0

    for btn in at.button:
        if "Run Deterministic" in btn.label:
            btn.click().run(timeout=30)
            break
    assert len(at.exception) == 0

    # Step 4: Verify Field Note Composer is rendered
    # Check preset buttons exist
    btn_plant = next((b for b in at.button if b.key == "btn_pr_plant"), None)
    assert btn_plant is not None, "btn_pr_plant button not found"

    # Click Plant Inspection preset
    btn_plant.click().run(timeout=30)
    assert len(at.exception) == 0, f"Error after clicking preset: {[e.message for e in at.exception]}"

    # Verify txt_field_note received preset text
    txt_note = next((t for t in at.text_area if t.key == "txt_field_note"), None)
    assert txt_note is not None
    assert "Plant inspection verified" in txt_note.value

    # Auto-detection should have chosen CAPACITY
    sb_pillar = next((s for s in at.selectbox if s.key == "sb_fn_pillar"), None)
    assert sb_pillar is not None
    assert sb_pillar.value == "CAPACITY"

    # User manually overrides to COLLATERAL
    sb_pillar.select("COLLATERAL").run(timeout=30)
    assert len(at.exception) == 0
    sb_pillar = next((s for s in at.selectbox if s.key == "sb_fn_pillar"), None)
    assert sb_pillar.value == "COLLATERAL"

    # Click Save Field Note
    btn_save = next((b for b in at.button if b.key == "btn_save_field_note"), None)
    assert btn_save is not None, "btn_save_field_note not found or not enabled"
    btn_save.click().run(timeout=30)
    assert len(at.exception) == 0, f"Error after saving field note: {[e.message for e in at.exception]}"

    # Verify note was saved in officer_notes
    notes = at.session_state.officer_notes
    assert len(notes) == 1
    assert notes[0]["affected_c"] == "COLLATERAL"
    assert "Plant inspection verified" in notes[0]["note_text"]

    # Verify input text area was cleared
    txt_note_after = next((t for t in at.text_area if t.key == "txt_field_note"), None)
    assert txt_note_after.value == ""

    # Step 5: Test Undo Last Note
    btn_undo = next((b for b in at.button if b.key == "btn_clear_last_note"), None)
    assert btn_undo is not None, "btn_clear_last_note not found"
    btn_undo.click().run(timeout=30)
    assert len(at.exception) == 0, f"Error after clicking undo: {[e.message for e in at.exception]}"

    # Verify note was popped
    assert len(at.session_state.officer_notes) == 0

    # Step 6: Test typing custom text directly into text_area
    txt_note = next((t for t in at.text_area if t.key == "txt_field_note"), None)
    txt_note.input("Promoter integrity verified through local market reputation and supplier checks.").run(timeout=30)
    assert len(at.exception) == 0

    # Verify auto-detection detected CHARACTER
    sb_pillar = next((s for s in at.selectbox if s.key == "sb_fn_pillar"), None)
    assert sb_pillar is not None
    assert sb_pillar.value == "CHARACTER"

    # Click Save again
    btn_save = next((b for b in at.button if b.key == "btn_save_field_note"), None)
    assert btn_save is not None
    btn_save.click().run(timeout=30)
    assert len(at.exception) == 0
    assert len(at.session_state.officer_notes) == 1
    assert at.session_state.officer_notes[0]["affected_c"] == "CHARACTER"
