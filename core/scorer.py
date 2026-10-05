from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional

from core.policy import (
    Verdict,
    WEIGHTS,
    WEAKEST_LINK_THRESHOLD,
    COMPOSITE_APPROVE_CUTOFF,
    COMPOSITE_CONDITIONAL_CUTOFF,
    DSCR_KNOCKOUT_THRESHOLD,
    BOUNCE_KNOCKOUT_THRESHOLD,
    DSCR_BANDS,
    DE_BANDS,
    BOUNCE_BANDS,
    ACCRUAL_CASH_GAP_BANDS,
    COLLATERAL_COVERAGE_BANDS,
)
from core.analyser import (
    calculate_monthly_emi,
    calculate_dscr,
    calculate_accrual_cash_gap,
)
from core.data_models import CreditCase


@dataclass
class ScoreDeduction:
    """Structured record of an auditable score deduction for forensic explainability."""
    rule_id: str
    pillar: str
    points_deducted: Decimal
    observed_value: Any
    threshold: Any
    explanation: str

    def to_string(self) -> str:
        return f"[{self.rule_id}] {self.pillar}: -{self.points_deducted} pts | Observed: {self.observed_value} (Threshold: {self.threshold}) -> {self.explanation}"


@dataclass
class GateResult:
    """Result of the deterministic final policy gate."""
    baseline_verdict: Verdict
    final_verdict: Verdict
    cap_applied: Optional[str] = None
    knockout_applied: Optional[str] = None
    policy_reasons: List[str] = field(default_factory=list)


@dataclass
class ScoreResult:
    """Complete credit assessment payload produced by the deterministic scoring engine."""
    five_cs: Dict[str, Decimal]
    composite_score: Decimal
    baseline_verdict: Verdict
    final_verdict: Verdict
    cap_applied: Optional[str] = None
    knockout_applied: Optional[str] = None
    dscr: Decimal = Decimal("0.0")
    proposed_monthly_emi: Decimal = Decimal("0.0")
    proposed_annual_emi: Decimal = Decimal("0.0")
    deductions: List[ScoreDeduction] = field(default_factory=list)
    policy_reasons: List[str] = field(default_factory=list)


def final_policy_gate(
    target: Any = None,
    five_cs: Optional[Dict[str, Decimal]] = None,
    dscr: Optional[Decimal] = None,
    bounces: Optional[int] = None,
    severe_flags: Optional[List[str]] = None,
    *,
    composite_score: Optional[Decimal] = None,
    research_findings: Optional[List[str]] = None
) -> Any:
    """
    Pure policy gate function.
    Deterministic minimum-verdict enforcement:
    1. Baseline verdict derived from composite score cutoffs.
    2. Weakest-link floor: Any operational C (Character, Capacity, Capital, Conditions) < 40 caps verdict at CONDITIONAL.
    3. Hard knock-outs (force REJECT):
       - DSCR < 1.20 (unless debt-free / Infinity coverage)
       - Bounce count >= 5 in 12-month window
       - Severe research findings (e.g. Insolvency / NCLT SEVERE_FLAG)
    Pure function: no mutation of inputs, zero side effects.
    Accepts either (ScoreResult, severe_flags) returning a new ScoreResult,
    or (composite_score, five_cs, dscr, bounces, severe_flags) returning GateResult.
    """
    flags = severe_flags if severe_flags is not None else (research_findings if research_findings is not None else [])

    if isinstance(target, ScoreResult):
        # Pure copy & update of existing ScoreResult
        bounce_val = 0
        for d in target.deductions:
            if d.rule_id == "RULE_CHAR_BOUNCES":
                bounce_val = int(d.observed_value)
                break

        gate_res = final_policy_gate(
            composite_score=target.composite_score,
            five_cs=target.five_cs,
            dscr=target.dscr,
            bounces=bounce_val,
            severe_flags=flags
        )
        return ScoreResult(
            five_cs=dict(target.five_cs),
            composite_score=target.composite_score,
            baseline_verdict=gate_res.baseline_verdict,
            final_verdict=gate_res.final_verdict,
            cap_applied=gate_res.cap_applied,
            knockout_applied=gate_res.knockout_applied,
            dscr=target.dscr,
            proposed_monthly_emi=target.proposed_monthly_emi,
            proposed_annual_emi=target.proposed_annual_emi,
            deductions=list(target.deductions),
            policy_reasons=list(gate_res.policy_reasons)
        )

    # Positional or keyword arguments
    comp = target if isinstance(target, Decimal) else composite_score
    if comp is None or five_cs is None or dscr is None or bounces is None:
        raise ValueError("Missing parameters for final_policy_gate: composite_score, five_cs, dscr, and bounces required")

    # Baseline Verdict from Composite Score
    if comp >= COMPOSITE_APPROVE_CUTOFF:
        baseline_verdict = Verdict.APPROVE
    elif comp >= COMPOSITE_CONDITIONAL_CUTOFF:
        baseline_verdict = Verdict.CONDITIONAL
    else:
        baseline_verdict = Verdict.REJECT

    final_verdict = baseline_verdict
    cap_applied = None
    knockout_applied = None
    policy_reasons: List[str] = []

    # A. The Weakest-Link Floor (Applies to Character, Capacity, Capital, Conditions)
    operational_pillars = ["CHARACTER", "CAPACITY", "CAPITAL", "CONDITIONS"]
    for pillar in operational_pillars:
        pillar_score = five_cs.get(pillar, Decimal("0.0"))
        if pillar_score < WEAKEST_LINK_THRESHOLD:
            if final_verdict > Verdict.CONDITIONAL:
                final_verdict = Verdict.CONDITIONAL
                cap_applied = f"WEAKEST_LINK_CAP ({pillar} < {WEAKEST_LINK_THRESHOLD})"
            policy_reasons.append(
                f"Weakest-Link Cap: {pillar} score ({pillar_score:.1f}) fell below {WEAKEST_LINK_THRESHOLD} threshold. Verdict capped at CONDITIONAL."
            )

    # B. Hard Knock-Outs (Force Verdict.REJECT)
    # Knock-Out 1: DSCR < 1.20
    if dscr != Decimal("Infinity") and dscr < DSCR_KNOCKOUT_THRESHOLD:
        final_verdict = Verdict.REJECT
        knockout_applied = f"DSCR_KNOCKOUT ({dscr:.2f} < {DSCR_KNOCKOUT_THRESHOLD})"
        policy_reasons.append(
            f"Hard Knock-Out: Operating DSCR ({dscr:.2f}) is below {DSCR_KNOCKOUT_THRESHOLD} minimum debt service hurdle. Automatic REJECT."
        )

    # Knock-Out 2: Habitual Bounces (>= 5 in 12 months)
    if bounces >= BOUNCE_KNOCKOUT_THRESHOLD:
        final_verdict = Verdict.REJECT
        knockout_applied = f"BOUNCE_KNOCKOUT ({bounces} >= {BOUNCE_KNOCKOUT_THRESHOLD})"
        policy_reasons.append(
            f"Hard Knock-Out: Cheque/Mandate bounce frequency ({bounces}) meets or exceeds maximum tolerance of {BOUNCE_KNOCKOUT_THRESHOLD} in 12 months. Automatic REJECT."
        )

    # Knock-Out 3: Secondary Research Severe Risk Flags (e.g. Insolvency / NCLT)
    if flags:
        for flag in flags:
            flag_upper = flag.upper()
            if "INSOLVENCY" in flag_upper or "NCLT" in flag_upper or "SEVERE" in flag_upper:
                final_verdict = Verdict.REJECT
                knockout_applied = f"RESEARCH_SEVERE_FLAG ({flag})"
                policy_reasons.append(
                    f"Hard Knock-Out: Secondary research identified severe legal/solvency risk: {flag}. Automatic REJECT."
                )
                break

    return GateResult(
        baseline_verdict=baseline_verdict,
        final_verdict=final_verdict,
        cap_applied=cap_applied,
        knockout_applied=knockout_applied,
        policy_reasons=policy_reasons
    )


def check_missing_financial_fields(credit_case: Optional[CreditCase]) -> List[str]:
    """Inspects CreditCase for any missing top-level objects or field-level parameters."""
    if credit_case is None:
        return ["credit_case"]

    missing = []
    # Facility inputs
    if credit_case.requested_loan_amount is None:
        missing.append("credit_case.requested_loan_amount")
    if credit_case.proposed_emi is None:
        missing.append("credit_case.proposed_emi")
    if credit_case.proposed_rate is None:
        missing.append("credit_case.proposed_rate")
    if credit_case.proposed_tenure_months is None:
        missing.append("credit_case.proposed_tenure_months")

    # Bank inputs
    if credit_case.bank_data is None:
        missing.append("bank_data")
    else:
        bank_fields = ["total_credits", "total_debits", "opening_balance", "closing_balance", "bounce_count"]
        for f in bank_fields:
            if getattr(credit_case.bank_data, f, None) is None:
                missing.append(f"bank_data.{f}")

    # GST inputs
    if credit_case.gst_data is None:
        missing.append("gst_data")
    else:
        gst_fields = ["taxable_value", "total_tax_paid"]
        for f in gst_fields:
            if getattr(credit_case.gst_data, f, None) is None:
                missing.append(f"gst_data.{f}")

    # ITR inputs
    if credit_case.itr_data is None:
        missing.append("itr_data")
    else:
        itr_fields = [
            "gross_total_income",
            "profit_after_tax",
            "depreciation",
            "interest_paid",
            "net_worth",
            "total_debt",
            "existing_emi_obligations"
        ]
        for f in itr_fields:
            if getattr(credit_case.itr_data, f, None) is None:
                missing.append(f"itr_data.{f}")

    return missing


def score_credit_case(
    credit_case: Optional[CreditCase],
    business_vintage_years: int = 5,
    pan_entity_mismatch: bool = False,
    collateral_value: Decimal = Decimal("0.00"),
    cgtmse_covered: bool = True,
    high_risk_sector: bool = False,
    severe_flags: Optional[List[str]] = None
) -> ScoreResult:
    """
    Computes preliminary Five Cs score and applies the Final Policy Gate.
    100% deterministic Python Decimal math. Zero LLM hallucinations.
    Routes missing top-level or field-level data to INSUFFICIENT_DATA path without exceptions.
    """
    missing = check_missing_financial_fields(credit_case)
    if missing:
        return ScoreResult(
            five_cs={
                "CHARACTER": Decimal("0.0"),
                "CAPACITY": Decimal("0.0"),
                "CAPITAL": Decimal("0.0"),
                "COLLATERAL": Decimal("0.0"),
                "CONDITIONS": Decimal("0.0")
            },
            composite_score=Decimal("0.0"),
            baseline_verdict=Verdict.INSUFFICIENT_DATA,
            final_verdict=Verdict.INSUFFICIENT_DATA,
            policy_reasons=[
                f"INSUFFICIENT_DATA: Missing mandatory extraction payload ({', '.join(missing)}) - routed for officer review."
            ]
        )

    bank = credit_case.bank_data
    gst = credit_case.gst_data
    itr = credit_case.itr_data

    deductions: List[ScoreDeduction] = []

    # ------------------------------------------------------------------------
    # 0. ANNUITY PROPOSED EMI COMPUTATION
    # ------------------------------------------------------------------------
    monthly_emi = calculate_monthly_emi(
        principal=credit_case.requested_loan_amount,
        annual_rate_pct=credit_case.proposed_rate,
        tenure_months=credit_case.proposed_tenure_months
    )
    annual_proposed_emi = monthly_emi * Decimal("12")

    # ------------------------------------------------------------------------
    # 1. CHARACTER (Weight: 20%)
    # ------------------------------------------------------------------------
    char_score = Decimal("100.0")
    bounces = bank.bounce_count

    # Bounce deductions (12-month window)
    bounce_penalty = Decimal("0.0")
    for min_b, max_b, penalty in BOUNCE_BANDS:
        if min_b <= bounces <= max_b:
            bounce_penalty = penalty
            break
    if bounce_penalty > Decimal("0"):
        char_score -= bounce_penalty
        deductions.append(ScoreDeduction(
            rule_id="RULE_CHAR_BOUNCES",
            pillar="CHARACTER",
            points_deducted=bounce_penalty,
            observed_value=bounces,
            threshold="0 Bounces",
            explanation=f"Cheque and mandate returns in 12-month statement window: {bounces}"
        ))

    # Vintage penalty
    if business_vintage_years < 3:
        char_score -= Decimal("20.0")
        deductions.append(ScoreDeduction(
            rule_id="RULE_CHAR_VINTAGE",
            pillar="CHARACTER",
            points_deducted=Decimal("20.0"),
            observed_value=f"{business_vintage_years} yrs",
            threshold=">= 3 yrs",
            explanation="Business operating vintage under 3 years carries statistical default hazard"
        ))

    # Entity consistency penalty
    if pan_entity_mismatch:
        char_score -= Decimal("15.0")
        deductions.append(ScoreDeduction(
            rule_id="RULE_CHAR_PAN_MISMATCH",
            pillar="CHARACTER",
            points_deducted=Decimal("15.0"),
            observed_value="FLAG_MISMATCH",
            threshold="CONSISTENT",
            explanation="PAN 4th character does not match MCA corporate entity classification"
        ))

    char_score = max(Decimal("0.0"), min(Decimal("100.0"), char_score))

    # ------------------------------------------------------------------------
    # 2. CAPACITY (Weight: 30%)
    # ------------------------------------------------------------------------
    ocf = itr.profit_after_tax + itr.depreciation + itr.interest_paid
    dscr_val = calculate_dscr(
        profit_after_tax=itr.profit_after_tax,
        depreciation=itr.depreciation,
        interest_paid=itr.interest_paid,
        annual_existing_emi=itr.existing_emi_obligations,
        annual_proposed_emi=annual_proposed_emi
    )

    cap_score = Decimal("20.0")
    if dscr_val == Decimal("Infinity"):
        # Infinite DSCR (zero debt service) gives full Capacity credit ONLY if OCF > 0
        cap_score = Decimal("100.0")
    elif dscr_val == Decimal("-Infinity") or ocf < Decimal("0.0"):
        # Operating cash flow is negative: score by cash-flow sign -> 0.0
        cap_score = Decimal("0.0")
    else:
        for min_d, max_d, score_val in DSCR_BANDS:
            if min_d <= dscr_val < max_d or (max_d == Decimal("Infinity") and dscr_val >= min_d):
                cap_score = score_val
                break

    if cap_score < Decimal("100.0"):
        explanation = "Debt Service Coverage Ratio below pristine banking benchmark"
        if ocf < Decimal("0.0") or dscr_val == Decimal("-Infinity"):
            explanation = f"Operating Cash Flow deficit (₹ {ocf}) prevents sustainable debt servicing"
        deductions.append(ScoreDeduction(
            rule_id="RULE_CAPACITY_DSCR",
            pillar="CAPACITY",
            points_deducted=Decimal("100.0") - cap_score,
            observed_value=f"DSCR {dscr_val:.2f}" if dscr_val not in (Decimal("Infinity"), Decimal("-Infinity")) else f"{dscr_val}",
            threshold="DSCR >= 1.50",
            explanation=explanation
        ))

    # ------------------------------------------------------------------------
    # 3. CAPITAL (Weight: 20%)
    # ------------------------------------------------------------------------
    if itr.net_worth <= Decimal("0"):
        capital_score = Decimal("0.0")
        deductions.append(ScoreDeduction(
            rule_id="RULE_CAPITAL_INSOLVENT",
            pillar="CAPITAL",
            points_deducted=Decimal("100.0"),
            observed_value=f"Net Worth ₹ {itr.net_worth}",
            threshold="Net Worth > 0",
            explanation="Negative or zero Net Worth indicates technical balance sheet insolvency"
        ))
    else:
        de_ratio = (itr.total_debt / itr.net_worth).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        capital_score = Decimal("20.0")
        for min_de, max_de, score_val in DE_BANDS:
            if min_de <= de_ratio < max_de or (max_de == Decimal("Infinity") and de_ratio >= min_de):
                capital_score = score_val
                break

        if capital_score < Decimal("100.0"):
            deductions.append(ScoreDeduction(
                rule_id="RULE_CAPITAL_LEVERAGE",
                pillar="CAPITAL",
                points_deducted=Decimal("100.0") - capital_score,
                observed_value=f"D/E {de_ratio:.2f}x",
                threshold="D/E <= 1.0x",
                explanation="Gearing / Debt-to-Equity ratio exceeds prime conservative threshold"
            ))

    # ------------------------------------------------------------------------
    # 4. COLLATERAL (Weight: 15%)
    # ------------------------------------------------------------------------
    if cgtmse_covered and collateral_value == Decimal("0"):
        # CGTMSE Exemption: Government credit guarantee covers the facility
        collateral_score = Decimal("75.0")
    else:
        coverage = (collateral_value / credit_case.requested_loan_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        collateral_score = Decimal("20.0")
        for min_cov, max_cov, score_val in COLLATERAL_COVERAGE_BANDS:
            if min_cov <= coverage < max_cov or (max_cov == Decimal("Infinity") and coverage >= min_cov):
                collateral_score = score_val
                break

        if collateral_score < Decimal("100.0"):
            deductions.append(ScoreDeduction(
                rule_id="RULE_COLLATERAL_COVERAGE",
                pillar="COLLATERAL",
                points_deducted=Decimal("100.0") - collateral_score,
                observed_value=f"{coverage:.2f}x Coverage",
                threshold=">= 1.25x",
                explanation="Pledged tangible security does not fully satisfy 1.25x collateral cover"
            ))

    # ------------------------------------------------------------------------
    # 5. CONDITIONS (Weight: 15%)
    # ------------------------------------------------------------------------
    gap_ratio, gap_direction = calculate_accrual_cash_gap(
        gst_turnover=gst.taxable_value,
        bank_inward_credits=bank.total_credits
    )

    cond_score = Decimal("30.0")
    for min_gap, max_gap, score_val in ACCRUAL_CASH_GAP_BANDS:
        if min_gap <= gap_ratio < max_gap or (max_gap == Decimal("Infinity") and gap_ratio >= min_gap):
            cond_score = score_val
            break

    if cond_score < Decimal("100.0"):
        deductions.append(ScoreDeduction(
            rule_id="RULE_CONDITIONS_GST_BANK_GAP",
            pillar="CONDITIONS",
            points_deducted=Decimal("100.0") - cond_score,
            observed_value=f"Gap {(gap_ratio * Decimal('100')):.1f}%",
            threshold="Gap <= 15%",
            explanation=f"Variance between GST returns and Bank statement: {gap_direction}"
        ))

    if high_risk_sector:
        cond_score = max(Decimal("0.0"), cond_score - Decimal("15.0"))
        deductions.append(ScoreDeduction(
            rule_id="RULE_CONDITIONS_SECTOR_RISK",
            pillar="CONDITIONS",
            points_deducted=Decimal("15.0"),
            observed_value="High Risk Sector",
            threshold="Neutral Sector",
            explanation="Industry sector classified under heightened macro credit surveillance"
        ))

    # ------------------------------------------------------------------------
    # 6. COMPOSITE SCORE CALCULATION
    # ------------------------------------------------------------------------
    five_cs = {
        "CHARACTER": char_score,
        "CAPACITY": cap_score,
        "CAPITAL": capital_score,
        "COLLATERAL": collateral_score,
        "CONDITIONS": cond_score
    }

    composite = sum(
        five_cs[pillar] * WEIGHTS[pillar] for pillar in WEIGHTS
    ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    # ------------------------------------------------------------------------
    # 7. FINAL POLICY GATE (MINIMUM VERDICT ENFORCEMENT)
    # ------------------------------------------------------------------------
    gate_result = final_policy_gate(
        composite_score=composite,
        five_cs=five_cs,
        dscr=dscr_val,
        bounces=bounces,
        severe_flags=severe_flags
    )

    return ScoreResult(
        five_cs=five_cs,
        composite_score=composite,
        baseline_verdict=gate_result.baseline_verdict,
        final_verdict=gate_result.final_verdict,
        cap_applied=gate_result.cap_applied,
        knockout_applied=gate_result.knockout_applied,
        dscr=dscr_val if dscr_val not in (Decimal("Infinity"), Decimal("-Infinity")) else (Decimal("999.99") if dscr_val == Decimal("Infinity") else Decimal("0.00")),
        proposed_monthly_emi=monthly_emi,
        proposed_annual_emi=annual_proposed_emi,
        deductions=deductions,
        policy_reasons=gate_result.policy_reasons
    )
