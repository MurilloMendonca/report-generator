"""Account-owned connection definitions and bounded, parameterized datasets."""
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$")]
ParameterName = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")]
Scalar = Annotated[str, StringConstraints(max_length=4096, pattern=r"^[^\x00]*$")] | int | float | bool | None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ApiConnectionConfig(StrictModel):
    base_url: str = Field(min_length=1, max_length=2048)
    auth_mode: Literal["none", "bearer", "api_key", "basic"] = "none"
    header_name: str = Field(default="X-API-Key", pattern=r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
    username: str = Field(default="", max_length=200)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.port not in {None, 443}
                or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
            raise ValueError("Use an HTTPS base URL without credentials, query parameters or fragments")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_auth(self):
        if self.auth_mode == "api_key" and (self.header_name.lower() in {
            "host", "cookie", "content-length", "transfer-encoding", "connection", "proxy-authorization",
            "accept", "accept-encoding", "user-agent", "upgrade", "te", "trailer",
        } or self.header_name.lower().startswith(("proxy-", "sec-"))):
            raise ValueError("This HTTP header cannot contain an API credential")
        if self.auth_mode == "basic" and (not self.username or ":" in self.username):
            raise ValueError("Basic authentication requires a username without a colon")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.username):
            raise ValueError("Invalid username")
        return self


class PostgresConnectionConfig(StrictModel):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value) or value.startswith("/"):
            raise ValueError("Use one hostname or IP address")
        return value

    @field_validator("database", "username")
    @classmethod
    def validate_identifier(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value) or "=" in value or "://" in value:
            raise ValueError("Connection strings are not accepted in connection fields")
        return value


class ConnectionBody(StrictModel):
    name: Name
    kind: Literal["api", "postgres"]
    config: dict
    secret: SecretStr | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def validate_config(self):
        model = ApiConnectionConfig if self.kind == "api" else PostgresConnectionConfig
        self.config = model.model_validate(self.config).model_dump()
        if self.secret is not None and any(ord(char) < 32 or ord(char) == 127 for char in self.secret.get_secret_value()):
            raise ValueError("Invalid secret")
        return self


class ApiDataset(StrictModel):
    endpoint: str = Field(default="", max_length=2048)
    records_path: str = Field(default="", max_length=1024)
    parameters: dict[ParameterName, Scalar] = Field(default_factory=dict, max_length=100)
    pagination: Literal["none", "next", "page"] = "none"
    next_path: str = Field(default="", max_length=1024)
    page_parameter: str = Field(default="page", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    first_page: int = Field(default=1, ge=0, le=1000000)
    max_pages: int = Field(default=20, ge=1, le=100)

    @field_validator("endpoint")
    @classmethod
    def relative_endpoint(cls, value):
        parsed = urlsplit(value)
        if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in value
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
            raise ValueError("Use a relative endpoint and put query parameters in parameters")
        return value

    @field_validator("records_path", "next_path")
    @classmethod
    def json_pointer(cls, value):
        if value and (not value.startswith("/") or "~" in value.replace("~0", "").replace("~1", "")):
            raise ValueError("Use a JSON Pointer")
        return value

    @model_validator(mode="after")
    def pagination_contract(self):
        if self.pagination == "next" and not self.next_path:
            raise ValueError("Next-link pagination requires next_path")
        return self


class PostgresDataset(StrictModel):
    mode: Literal["table", "query"] = "table"
    schema_name: str = Field(default="public", min_length=1, max_length=128)
    table_name: str = Field(default="", max_length=128)
    query: str = Field(default="", max_length=20000)
    parameters: dict[ParameterName, Scalar] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def query_contract(self):
        if any("\x00" in value for value in [self.schema_name, self.table_name, self.query]):
            raise ValueError("NUL characters are not allowed")
        if self.mode == "table" and not self.table_name:
            raise ValueError("Choose a table or view")
        if self.mode == "query" and not re.match(r"^\s*(SELECT|WITH)\b", self.query, re.I):
            raise ValueError("Use a SELECT query; parameters use %(name)s placeholders")
        return self


class DatasetBody(StrictModel):
    name: Name
    connection_id: UUID = Field(strict=False)
    definition: dict


class SourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    dataset_id: UUID
    parameters: dict[ParameterName, Scalar] = Field(default_factory=dict, max_length=100)


class SourceSelections(BaseModel):
    sources: list[SourceSelection] = Field(min_length=1, max_length=10)
