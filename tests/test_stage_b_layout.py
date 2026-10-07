import sys
from decimal import Decimal
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.analyser import calculate_monthly_emi
from core.formatting import format_inr
from core.policy import Verdict

APP_PATH = str(BASE_DIR / "app.py")


def test_facility_persistence_across_navigation_and_dscr_reflection():
    """
    Stage B Persistence Test:
    - Set facility terms on Case page form
    - Click Apply Facility Terms
    - Navigate Case -> Evidence -> Decision without touching facility inputs
    - Check terms match what was entered and DSCR reflects them
    """
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run(timeout=30)

    # 1. On Case page: verify initial defaults
    assert float(at.session_state.facility_loan_amount) == 5000000.0
    assert int(at.session_state.facility_tenure_months) == 36
    assert float(at.session_state.facility_interest_rate) == 11.5

    # 2. Set new facility terms on Case form
    new_loan = 7500000.0
    new_tenure = 48
    new_rate = 12.0
    new_collat = 2500000.0
    new_cgtmse = False

    at.number_input[0].set_value(new_loan)
    at.slider[0].set_value(new_tenure)
    at.slider[1].set_value(new_rate)
    at.number_input[1].set_value(new_collat)
    at.checkbox[0].set_value(new_cgtmse)

    apply_btn = next(b for b in at.button if "Apply Facility" in b.label)
    apply_btn.click().run(timeout=30)

    # Verify plain session_state keys updated
    assert float(at.session_state.facility_loan_amount) == new_loan
    assert int(at.session_state.facility_tenure_months) == new_tenure
    assert float(at.session_state.facility_interest_rate) == new_rate
    assert float(at.session_state.facility_collateral_val) == new_collat
    assert bool(at.session_state.facility_cgtmse_covered) == new_cgtmse

    # Verify plain alias keys also match
    assert float(at.session_state.loan_amount) == new_loan
    assert int(at.session_state.tenure_months) == new_tenure

    # Expected EMI calculation
    expected_emi = calculate_monthly_emi(
        principal=Decimal(str(new_loan)),
        annual_rate_pct=Decimal(str(new_rate)),
        tenure_months=new_tenure,
    )
    assert float(at.session_state.app_context["computed_monthly_emi"]) == float(expected_emi)

    # 3. Navigate Case -> Evidence (WITHOUT touching facility inputs)
    at.switch_page("app_pages/evidence.py").run(timeout=30)

    # Run extraction and lock financials
    next(b for b in at.button if "Extract" in b.label).click().run(timeout=30)
    next(b for b in at.button if "Confirm & Lock" in b.label).click().run(timeout=30)
    assert at.session_state.locked_case is not None

    # 4. Navigate Evidence -> Decision (WITHOUT touching facility inputs)
    at.switch_page("app_pages/decision.py").run(timeout=30)

    # Verify plain session_state keys still retain the submitted values
    assert float(at.session_state.facility_loan_amount) == new_loan
    assert int(at.session_state.facility_tenure_months) == new_tenure
    assert float(at.session_state.facility_interest_rate) == new_rate
    assert float(at.session_state.facility_collateral_val) == new_collat
    assert bool(at.session_state.facility_cgtmse_covered) == new_cgtmse

    # Verify read-only facility strip shows the exact entered values
    strip_markdowns = [m.value for m in at.markdown if "Facility:" in m.value]
    assert len(strip_markdowns) >= 1
    strip_text = strip_markdowns[0]
    assert "75,00,000" in strip_text
    assert "48 mos @ 12.0%" in strip_text
    assert "25,00,000" in strip_text
    assert "CGTMSE:</strong> No" in strip_text

    # 5. Run Deterministic Risk Engine on Decision page
    score_btn = next(b for b in at.button if "Run Deterministic" in b.label)
    score_btn.click().run(timeout=30)

    sr = at.session_state.score_result
    assert sr is not None
    assert sr.proposed_monthly_emi == expected_emi

    # Verify DSCR reflects the entered terms:
    # Operating cash flow / proposed annual EMI
    expected_annual_debt = expected_emi * Decimal("12")
    assert sr.proposed_annual_emi == expected_annual_debt
    # DSCR is non-zero and computed with the new annual debt service
    assert sr.dscr > Decimal("0")
    # For Cohort 1 (Apex net operating cash flow), 7.5M@12% 48M gives monthly 197,503.77 -> annual 2,370,045.24 -> DSCR = 2.7472
    assert sr.dscr == Decimal("2.7472")
