from pydantic import BaseModel, Field
from typing import Optional


class ParticipantCreate(BaseModel):
    start_number: int = Field(..., gt=0)
    first_name: str
    last_name: str
    birth_year: int
    club: Optional[str] = None
    team: Optional[str] = None


class ParticipantResponse(BaseModel):
    id: int
    start_number: int
    first_name: str
    last_name: str
    birth_year: int
    race_class: int
    club: Optional[str] = None
    team: Optional[str] = None
    checked_in: bool
    active: bool

    model_config = {
        "from_attributes": True
    }


class PairingResponse(BaseModel):
    id: int
    race_class: int
    round_name: str
    pairing_number: int
    driver_1: ParticipantResponse
    driver_2: Optional[ParticipantResponse] = None
    is_single_driver: bool
    status: str

    model_config = {
        "from_attributes": True
    }