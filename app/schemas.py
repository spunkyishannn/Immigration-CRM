from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ClientBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str = Field(min_length=2, max_length=120)
    # Keep optional in practice to avoid blocking case creation workflows.
    email: str = ""
    phone: str = ""
    country: str = ""
    visa_type: str = Field(default="Full Time - Work Permit", min_length=2, max_length=60)
    stage: Literal[
        "Documentation Done",
        "Permit Under Process",
        "Permit Approved",
        "Waiting for Visa Decision",
    ]
    nationality: str = "Indian"
    passport: str = ""
    process_started_on: Optional[str] = None
    total_fee: float = 0
    notes: str = ""
    recruiting_partners: Optional[str] = None


class ClientCreate(ClientBase):
    pass


class ClientUpdate(ClientBase):
    pass


class ClientOut(ClientBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: str
    updated_at: str


class PaymentBase(BaseModel):
    client_id: int
    description: str = ""
    amount_paid: float = 0
    payment_type: Literal["UPI", "Cash", "Bank Transfer"]
    collected_by: str = ""
    paid_at: Optional[str] = None


class PaymentCreate(PaymentBase):
    pass


class PaymentUpdate(BaseModel):
    client_id: Optional[int] = None
    description: Optional[str] = None
    amount_paid: Optional[float] = None
    payment_type: Optional[Literal["UPI", "Cash", "Bank Transfer"]] = None
    collected_by: Optional[str] = None
    paid_at: Optional[str] = None
