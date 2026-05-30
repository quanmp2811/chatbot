from typing import Literal, Optional

from pydantic import BaseModel, field_validator


class CreateCompanyRequest(BaseModel):
    name: str
    owner_name: str
    shared_drive_id: Optional[str] = None
    creation_mode: Literal["paid", "trial"] = "paid"

    @field_validator("owner_name")
    @classmethod
    def validate_owner_name(cls, value: str) -> str:
        normalized = (value or "").strip()
        if any(char.isdigit() for char in normalized):
            raise ValueError("Họ tên người đại diện không được chứa số")
        return normalized
