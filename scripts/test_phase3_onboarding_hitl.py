import os
import sys
from pathlib import Path

# Add project root to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.onboarding import (
    validate_cin_syntax,
    parse_cin,
    validate_pan_syntax,
    check_entity_consistency,
    generate_application_id
)
from core.audit_logger import AuditLogger, mask_identifier


def test_onboarding_and_audit():
    print("=" * 70)
    print("RUNNING PHASE 3 AUTOMATED COMPLIANCE & UNIT VERIFICATION")
    print("=" * 70)

    # 1. Test Valid CIN
    cin_valid = "U29253DL2018PTC334120"
    is_ok, err = validate_cin_syntax(cin_valid)
    assert is_ok and err is None, f"Expected valid CIN, got {err}"
    parsed = parse_cin(cin_valid)
    assert parsed["incorporation_year"] == 2018, f"Wrong year: {parsed['incorporation_year']}"
    assert parsed["business_vintage_years"] == 8, f"Wrong vintage: {parsed['business_vintage_years']}"
    assert parsed["state_code"] == "DL", f"Wrong state: {parsed['state_code']}"
    assert parsed["entity_code"] == "PTC", f"Wrong entity: {parsed['entity_code']}"
    print(f"  • Valid CIN Parsing       : PASSED ({parsed['macro_sector']}, Vintage: {parsed['business_vintage_years']} yrs)")

    # 2. Test Invalid CINs (Edge Cases)
    invalid_cins = ["U1234DL", "X29253DL2018PTC334120", "U29253DL2018PTC33412", ""]
    for inv in invalid_cins:
        is_ok, err = validate_cin_syntax(inv)
        assert not is_ok, f"Expected failure for {inv}, but passed"
    print(f"  • Invalid CIN Syntax Gate : PASSED (Tested {len(invalid_cins)} edge cases)")

    # 3. Test Entity Consistency (PAN vs CIN)
    corp_pan = "AAACA1234B"
    indiv_pan = "AAAPA1234B"
    
    # Matching Company
    res_corp = check_entity_consistency(corp_pan, is_corporate=True, cin_entity_code="PTC")
    assert not res_corp["mismatch_detected"]
    assert res_corp["consistency_status"] == "CONSISTENT"
    print(f"  • Corp PAN Alignment      : PASSED (PAN 4th char 'C' matched Company)")

    # Mismatch: Individual PAN for Corporate Loan
    res_mismatch = check_entity_consistency(indiv_pan, is_corporate=True, cin_entity_code="PTC")
    assert res_mismatch["mismatch_detected"]
    assert res_mismatch["consistency_status"] == "FLAG_MISMATCH"
    print(f"  • Mismatch Trap Detection : PASSED (Caught Individual PAN 'P' for Corp loan)")

    # 4. Test Application ID Generation
    app_id = generate_application_id("Apex Synthetic Holdings (Demo)", "20260915")
    assert app_id.startswith("APP-20260915-")
    print(f"  • CRM Application Key Gen : PASSED ({app_id})")

    # 5. Test Audit Logger with PII Masking
    logger = AuditLogger(app_id)
    evt = logger.log(
        event_type="TEST_ONBOARDING",
        actor="OFFICER",
        details={"pan": corp_pan, "loan_amount": 5000000}
    )
    assert evt["details"]["pan"] == "AA***4B", f"PII not masked: {evt['details']['pan']}"
    
    events = logger.load_events()
    assert len(events) >= 1
    assert events[-1]["event_type"] == "TEST_ONBOARDING"
    print(f"  • Immutable Audit Stream  : PASSED (Masked PII: {evt['details']['pan']}, JSONL written)")

    print("=" * 70)
    print("ALL PHASE 3 COMPLIANCE & REASONING CHECKS PASSED.")
    print("=" * 70)


if __name__ == "__main__":
    test_onboarding_and_audit()
