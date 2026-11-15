from pydantic import BaseModel, ConfigDict, Field


class CreateApiToken(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100, pattern=r"^[^\x00]*\S[^\x00]*$",
                      description="Label shown in the account page, for example the script or tool using it.")
    expires_in_days: int = Field(default=90, ge=1, le=365, strict=True, description="Days until the token stops working.")
