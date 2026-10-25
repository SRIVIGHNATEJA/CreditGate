from pydantic import BaseModel, Field, field_validator
from decimal import Decimal, InvalidOperation
from typing import Optional, Any, List, Dict
from enum import Enum

import re

CRORE_SUFFIX_RE = re.compile(r"^(.*?)\s*(?:crores|crore|cr)\.?$", re.IGNORECASE)
LAKH_SUFFIX_RE = re.compile(r"^(.*?)\s*(?:lakhs|lakh|lacs|lac)\.?$", re.IGNORECASE)

def coerce_currency(v: Any) -> Decimal:
    """Safely coerces strings, ints, floats, and Indian numbering (Cr/Lakh) into exact Decimals.
    Handles standard accounting brackets (1,500) -> -1500.
    Never coerces None or missing inputs to zero."""
    if v is None:
        raise ValueError("Cannot coerce None to Decimal (missing input)")
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, str):
        v_clean = v.replace(",", "").replace("₹", "").replace("$", "").replace("−", "-").strip()
        v_lower = v_clean.lower()
        
        is_negative = False
        if v_lower.startswith('(') and v_lower.endswith(')'):
            is_negative = True
            v_lower = v_lower[1:-1].strip()

        if v_lower.startswith("inr"):
            v_lower = v_lower[3:].strip()
        elif v_lower.startswith("rs."):
            v_lower = v_lower[3:].strip()
        elif v_lower.startswith("rs"):
            v_lower = v_lower[2:].strip()

        if v_lower.startswith("-"):
            is_negative = not is_negative
            v_lower = v_lower[1:].strip()

        v_lower = v_lower.rstrip(",").strip()
            
        try:
            m_cr = CRORE_SUFFIX_RE.match(v_lower)
            m_lk = LAKH_SUFFIX_RE.match(v_lower)
            if m_cr:
                num = Decimal(m_cr.group(1).strip())
                val = num * Decimal("10000000")
            elif m_lk:
                num = Decimal(m_lk.group(1).strip())
                val = num * Decimal("100000")
            else:
                val = Decimal(v_lower)
                
            return -val if is_negative else val
        except InvalidOperation:
            raise ValueError(f"Could not parse currency value: {v}")
    raise ValueError(f"Invalid type for currency: {type(v)}")

class BankExtraction(BaseModel):
    total_credits: Optional[Decimal] = None
    total_debits: Optional[Decimal] = None
    opening_balance: Optional[Decimal] = None
    closing_balance: Optional[Decimal] = None
    bounce_count: Optional[int] = None
    confidence: str = Field(default="HIGH", pattern="^(HIGH|LOW)$")
    extraction_engine: Optional[str] = None
    redaction_events: List[Dict[str, Any]] = Field(default_factory=list)
    officer_verified: bool = False

    @field_validator('total_credits', 'total_debits', 'opening_balance', 'closing_balance', mode='before')
    def parse_money(cls, v):
        if v is None:
            return None
        return coerce_currency(v)

class GSTExtraction(BaseModel):
    taxable_value: Optional[Decimal] = None
    total_tax_paid: Optional[Decimal] = None
    confidence: str = Field(default="HIGH", pattern="^(HIGH|LOW)$")
    extraction_engine: Optional[str] = None
    redaction_events: List[Dict[str, Any]] = Field(default_factory=list)
    officer_verified: bool = False

    @field_validator('taxable_value', 'total_tax_paid', mode='before')
    def parse_money(cls, v):
        if v is None:
            return None
        return coerce_currency(v)

class ITRExtraction(BaseModel):
    gross_total_income: Optional[Decimal] = None
    profit_after_tax: Optional[Decimal] = None
    depreciation: Optional[Decimal] = None
    interest_paid: Optional[Decimal] = None
    net_worth: Optional[Decimal] = None
    total_debt: Optional[Decimal] = None
    existing_emi_obligations: Optional[Decimal] = None
    confidence: str = Field(default="HIGH", pattern="^(HIGH|LOW)$")
    extraction_engine: Optional[str] = None
    redaction_events: List[Dict[str, Any]] = Field(default_factory=list)
    officer_verified: bool = False

    @field_validator('gross_total_income', 'profit_after_tax', 'depreciation', 'interest_paid', 'net_worth', 'total_debt', 'existing_emi_obligations', mode='before')
    def parse_money(cls, v):
        if v is None:
            return None
        return coerce_currency(v)

class CreditCase(BaseModel):
    company_name: str
    cin_or_pan: str
    pan: Optional[str] = None
    requested_loan_amount: Optional[Decimal] = None
    proposed_emi: Optional[Decimal] = None
    proposed_rate: Optional[Decimal] = None
    proposed_tenure_months: Optional[int] = None
    collateral_value: Optional[Decimal] = Decimal("0.00")
    cgtmse_covered: bool = False
    officer_verified: bool = False
    
    bank_data: Optional[BankExtraction] = None
    gst_data: Optional[GSTExtraction] = None
    itr_data: Optional[ITRExtraction] = None

    @field_validator('requested_loan_amount', 'proposed_emi', 'proposed_rate', 'collateral_value', mode='before')
    def parse_money(cls, v):
        if v is None:
            return None
        return coerce_currency(v)


class ReviewStatus(str, Enum):
    """Tripartite review status enforcing Human-in-the-Loop governance."""
    UNREVIEWED = "UNREVIEWED"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"


class FindingTier(str, Enum):
    """Adverse Media severity tiers with strict policy consequences."""
    SEVERE = "SEVERE"              # Admitted Insolvency (NCLT Sec 7/9 Admitted, Liquidation Order) -> REJECT if confirmed
    REVIEW = "REVIEW"              # Statutory Inquiries (DGGI summons, tax notices, petitions) -> CONDITIONAL cap if confirmed
    INFORMATIONAL = "INFORMATIONAL"# General Adverse Media (Negative PR, executive turnover) -> Informational only


class AdverseMediaFinding(BaseModel):
    """Individual adverse media or legal hit."""
    id: str
    tier: FindingTier
    title: str
    snippet: str
    source_url: str
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    dismissal_reason: Optional[str] = None
    confirmation_note: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    is_synthetic: bool = False


class AdverseMediaReport(BaseModel):
    """Aggregated Adverse Media Screening payload."""
    status: str = "COMPLETED"  # "COMPLETED", "RESEARCH_UNAVAILABLE", "NO_ADVERSE_FINDINGS"
    findings: List[AdverseMediaFinding] = Field(default_factory=list)
    query_used: str = ""
    is_synthetic_demo: bool = False
    error_message: Optional[str] = None
    disclaimer: Optional[str] = None
