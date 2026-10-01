from pydantic import BaseModel, Field, field_validator
from decimal import Decimal, InvalidOperation
from typing import Optional, Any

def coerce_currency(v: Any) -> Decimal:
    """Safely coerces strings, ints, floats, and Indian numbering (Cr/Lakh) into exact Decimals.
    Handles standard accounting brackets (1,500) -> -1500."""
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, str):
        v_clean = v.replace(",", "").replace("₹", "").replace("$", "").strip()
        v_lower = v_clean.lower()
        
        is_negative = False
        if v_lower.startswith('(') and v_lower.endswith(')'):
            is_negative = True
            v_lower = v_lower[1:-1].strip()
            
        try:
            if "cr" in v_lower or "crore" in v_lower:
                num = Decimal(v_lower.replace("cr", "").replace("crore", "").strip())
                val = num * Decimal("10000000")
            elif "lakh" in v_lower or "lac" in v_lower:
                num = Decimal(v_lower.replace("lakh", "").replace("lac", "").strip())
                val = num * Decimal("100000")
            else:
                val = Decimal(v_lower)
                
            return -val if is_negative else val
        except InvalidOperation:
            raise ValueError(f"Could not parse currency value: {v}")
    raise ValueError(f"Invalid type for currency: {type(v)}")

class BankExtraction(BaseModel):
    total_credits: Decimal
    total_debits: Decimal
    opening_balance: Decimal
    closing_balance: Decimal
    bounce_count: int
    confidence: str = Field(pattern="^(HIGH|LOW)$")

    @field_validator('total_credits', 'total_debits', 'opening_balance', 'closing_balance', mode='before')
    def parse_money(cls, v):
        return coerce_currency(v)

class GSTExtraction(BaseModel):
    taxable_value: Decimal
    total_tax_paid: Decimal
    confidence: str = Field(pattern="^(HIGH|LOW)$")

    @field_validator('taxable_value', 'total_tax_paid', mode='before')
    def parse_money(cls, v):
        return coerce_currency(v)

class ITRExtraction(BaseModel):
    gross_total_income: Decimal
    profit_after_tax: Decimal
    depreciation: Decimal
    interest_paid: Decimal
    net_worth: Decimal
    existing_emi_obligations: Decimal
    confidence: str = Field(pattern="^(HIGH|LOW)$")

    @field_validator('gross_total_income', 'profit_after_tax', 'depreciation', 'interest_paid', 'net_worth', 'existing_emi_obligations', mode='before')
    def parse_money(cls, v):
        return coerce_currency(v)

class CreditCase(BaseModel):
    company_name: str
    cin_or_pan: str
    requested_loan_amount: Decimal
    proposed_emi: Decimal
    proposed_rate: Decimal
    proposed_tenure_months: int
    
    bank_data: Optional[BankExtraction] = None
    gst_data: Optional[GSTExtraction] = None
    itr_data: Optional[ITRExtraction] = None

    @field_validator('requested_loan_amount', 'proposed_emi', mode='before')
    def parse_money(cls, v):
        return coerce_currency(v)
