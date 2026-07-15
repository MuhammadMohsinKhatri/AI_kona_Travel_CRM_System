"""The structured output of the AI event classifier (mirrors the n8n v8.0 schema).

Kept as an explicit Pydantic model so both the live OpenAI adapter and the mock
produce identically shaped data, and so the billing engine has a stable contract.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Classification(BaseModel):
    EVENT_ID: str = ""
    EVENT_NAME: str = ""
    EVENT_DATE: str = ""
    EVENT_TYPE: str = ""          # invoice | selling | minimum guarantee | hybrid | undefined
    BILLING_MODEL: str = ""       # one of the 11 BILLING_MODELS (see core.billing)
    TAXABLE: str = "YES"          # "YES" | "NO"
    TAX_RATE_sales: float = 0.0
    PROCESSING_FEE_RATE: float = 0.0
    MG_SHORTFALL: float = 0.0
    PAYMENT_METHOD: str = "CHECK"  # CHECK | CASH | CREDIT_CARD
    PAID_STATUS: bool = False
    PRIMARY_WORKER: str = ""
    HOURS: float = 0.0
    TOTAL_EVENT_HOURS: float = 0.0
    HOURLY_RATE: float = 0.0
    ACTUAL_EVENT_START_TIME: str = ""
    ACTUAL_EVENT_END_TIME: str = ""
    ATTENDEE_COUNT: int = 0
    UNITS_SERVED_TOTAL: float = 0.0
    UNITS_INCLUDED_IN_BASE: float = 0.0
    BASE_AMOUNT: float = 0.0
    BASE_IS_FIXED_COMMITMENT: str = "TRUE"
    RATE_PER_SERVING: float = 0.0
    LOCATION_FEE: float = 0.0
    MINIMUM_AMOUNT_PER_HOUR: float = 0.0
    MINIMUM_FLAT_AMOUNT: float = 0.0
    GIVEBACK_PERCENTAGE: float = 0.0
    HOST_SUBSIDY_PER_SERVING: float = 0.0
    GUEST_RATE_PER_SERVING: float = 0.0
    DEPOSIT_AMOUNT: float = 0.0
    DISCOUNT_PERCENT: float = 0.0
    DISCOUNT_AMOUNT: float = 0.0
    SQUARE_USED: str = "FALSE"
    SQUARE_DEVICE_CONFIDENCE: str = "LOW"
    ASSIGNED_EQUIPMENT: str = ""
    DRIVER_REPORTED_EQUIPMENT: str = ""
    ACTUAL_TIME_FOUND: str = "FALSE"
    SERVING_COUNT_SOURCE: str = ""
    CASH_COLLECTED_DETECTED: str = "FALSE"
    CASH_COLLECTED_AMOUNT: float = 0.0
    CHECK_INVOICE_AMOUNT: float = 0.0
    NOTE: str = ""
    # Alert keys emitted by the classifier for the alert engine to interpret.
    ALERT: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}
