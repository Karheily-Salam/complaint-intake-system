"""Declarative models for complaint schemas.

These describe *what information a complaint type needs* and *how to validate it*.
They are loaded from YAML files in ``definitions/`` - business rules live there,
never in the conversation engine or in prompts.

Extension point
---------------
``ComplaintSchema.fields_for()`` is the single seam the engine uses to obtain a
complaint's field list. It currently returns a flat list. If country- or
method-specific requirements are introduced later (e.g. "a SEPA bank transfer in
DE also needs an IBAN"), they can be layered in here or in a wrapper registry
*without touching the conversation engine* - the engine only ever asks the schema
"what fields does this complaint need right now?".
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


class ComplaintSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str                              # ComplaintType value
    label: str
    description: str = ""
    # True for "other": no fixed field set; engine still produces a concise summary.
    open_schema: bool = False
    # For open schemas: minimum words the summarised problem description must have
    # before the engine treats it as sufficient (otherwise it asks for clarification).
    min_description_words: int = 6
    common_fields: list[FieldSpec] = Field(default_factory=list)

    def fields_for(self) -> list[FieldSpec]:
        """The ordered field list for this complaint.

        Flat today. This is the extension seam for future conditional
        (country/method-specific) requirements - see the module docstring.
        """
        return list(self.common_fields)

    def required_keys(self) -> list[str]:
        return [f.key for f in self.fields_for() if f.required]
