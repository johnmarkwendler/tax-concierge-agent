from typing import Any, Literal

from pydantic import BaseModel, Field

EntityPath = Literal[
    "Sole Proprietor",
    "Single-Member LLC",
    "Partnership",
    "S-Corp",
    "C-Corp",
    "Cannot Determine Yet",
]


UIComponentType = Literal[
    "radio",
    "select",
    "text_input",
    "date_input",
    "document_upload",
]


class UploadedDocument(BaseModel):
    name: str
    document_type: str | None = None
    content: str | None = None
    ocr_text: str | None = None
    extracted_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UIComponent(BaseModel):
    id: str
    type: UIComponentType
    label: str
    options: list[str] = Field(default_factory=list)
    required: bool = True
    helper_text: str | None = None


class NextUI(BaseModel):
    schema_version: str = "a2ui-lite/v1"
    title: str
    explanation: str
    components: list[UIComponent]
    submit_label: str = "Continue"


class TaxIntake(BaseModel):
    user_story: str
    uploaded_documents: list[UploadedDocument] = Field(default_factory=list)
    known_facts: dict[str, Any] = Field(default_factory=dict)
    missing_facts: list[str] = Field(default_factory=list)
    candidate_entities: list[EntityPath] = Field(
        default_factory=lambda: ["Cannot Determine Yet"]
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    next_ui: NextUI | None = None
    explanation: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    recommendation_summary: str | None = None
    redacted_fields: list[str] = Field(default_factory=list)
    security_flags: list[str] = Field(default_factory=list)
    injection_detected: bool = False
    quarantined_content: list[str] = Field(default_factory=list)


class TaxWorkflowInput(BaseModel):
    data: dict[str, Any] | None = None
    story: str | None = None


class FactExtraction(BaseModel):
    summary: str
    known_facts: dict[str, Any] = Field(default_factory=dict)
    missing_facts: list[str] = Field(default_factory=list)
    ambiguity_notes: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    next_question_explanation: str = (
        "I need one more detail to route the entity correctly."
    )


class HumanInputResponse(BaseModel):
    field_id: str
    value: Any
