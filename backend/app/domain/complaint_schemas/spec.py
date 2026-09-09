"""Declarative models for complaint schemas.

These describe *what information a complaint type needs* and *how to validate it*.
They are loaded from YAML files in ``definitions/`` - business rules live there,
never in the conversation engine or in prompts.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FieldType(StrEnum):
    STRING = "string"
    TEXT = "text"
    EMAIL = "email"
    DATE = "date"
    ENUM = "enum"
    NUMBER = "number"


class FieldValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str | None = None            # regex the value must fully match
    min_length: int | None = None
    max_length: int | None = None
    enum_values: list[str] | None = None  # allowed values for FieldType.ENUM
    date_formats: list[str] | None = None  # accepted strptime formats for FieldType.DATE


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    type: FieldType = FieldType.STRING
    required: bool = True
    description: str = ""
    # Free-text hint handed to the AI extractor. Presentation only - no logic.
    extraction_hint: str = ""
    example: str | None = None
    validation: FieldValidation = Field(default_factory=FieldValidation)


class DepositMethodSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    description: str = ""
    additional_fields: list[FieldSpec] = Field(default_factory=list)


class ComplaintSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str                              # ComplaintType value
    label: str
    description: str = ""
    # True for "other": no fixed field set; engine still produces a concise summary.
    open_schema: bool = False
    common_fields: list[FieldSpec] = Field(default_factory=list)
    # Only meaningful for schemas that branch on a method (e.g. deposit).
    method_selector_label: str | None = None
    methods: list[DepositMethodSpec] = Field(default_factory=list)

    def method(self, key: str) -> DepositMethodSpec | None:
        return next((m for m in self.methods if m.key == key), None)

    def fields_for(self, method_key: str | None = None) -> list[FieldSpec]:
        """Full ordered field list for this complaint, optionally for a method."""
        fields = list(self.common_fields)
        if method_key:
            method = self.method(method_key)
            if method:
                fields.extend(method.additional_fields)
        return fields
