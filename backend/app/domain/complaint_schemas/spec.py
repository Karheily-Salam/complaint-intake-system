"""Declarative models for complaint schemas.

These describe *what information a complaint type needs* and *how to validate it*.
They are loaded from YAML files in ``definitions/`` - business rules live there,
never in the conversation engine or in prompts.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    # Natural-language phrases mapping onto each enum value, for providers that map
    # free-text wording onto a value. Populated from method aliases for method_field.
    enum_aliases: dict[str, list[str]] | None = None
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
    # Natural-language phrases that indicate this method. Used by providers to map
    # a customer's wording onto ``key``; kept in config so methods stay data-driven.
    aliases: list[str] = Field(default_factory=list)
    additional_fields: list[FieldSpec] = Field(default_factory=list)


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

    # ---- method branching (e.g. deposit) ----
    # The single enum field whose value selects a method. When present, it is
    # always part of the required field set; each method then contributes its own
    # ``additional_fields`` once that method is known.
    method_field: FieldSpec | None = None
    methods: list[DepositMethodSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_method_enum(self) -> ComplaintSchema:
        if self.methods and self.method_field is not None:
            keys = [m.key for m in self.methods]
            if not self.method_field.validation.enum_values:
                self.method_field.validation.enum_values = keys
            if not self.method_field.validation.enum_aliases:
                self.method_field.validation.enum_aliases = {
                    m.key: [m.label, *m.aliases] for m in self.methods
                }
            self.method_field.type = FieldType.ENUM
        if self.method_field is not None and not self.methods:
            raise ValueError("method_field defined but no methods listed")
        return self

    # ---- lookup helpers ----

    def method(self, key: str | None) -> DepositMethodSpec | None:
        if key is None:
            return None
        return next((m for m in self.methods if m.key == key), None)

    @property
    def method_keys(self) -> list[str]:
        return [m.key for m in self.methods]

    def method_aliases(self) -> dict[str, str]:
        """Map every alias (and the method key/label) to its method key."""
        mapping: dict[str, str] = {}
        for m in self.methods:
            for alias in [m.key, m.label, *m.aliases]:
                mapping[alias.lower()] = m.key
        return mapping

    def fields_for(self, method_key: str | None = None) -> list[FieldSpec]:
        """Ordered field list for this complaint, given a (possibly unknown) method.

        - always: ``common_fields`` (+ ``method_field`` when the schema branches)
        - additionally: the selected method's ``additional_fields`` once known
        """
        fields = list(self.common_fields)
        if self.method_field is not None:
            fields.append(self.method_field)
        method = self.method(method_key)
        if method:
            fields.extend(method.additional_fields)
        return fields

    def required_keys(self, method_key: str | None = None) -> list[str]:
        return [f.key for f in self.fields_for(method_key) if f.required]
