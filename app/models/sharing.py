from pydantic import BaseModel, ConfigDict, Field


class CreateShareLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="", max_length=100, pattern=r"^[^\x00]*$")
    expires_in_hours: int | None = Field(default=168, ge=1, le=8760, strict=True)
