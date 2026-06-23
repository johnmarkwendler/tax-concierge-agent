from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EntityPath = Literal[
    "Sole Proprietor",
    "Single-Member LLC",
    "Partnership",
    "S-Corp",
    "C-Corp",
    "Cannot Determine Yet",
]


ReadinessState = Literal[
    "Still learning",
    "Needs clarification",
    "Ready for recommendation",
    "Security review required",
]

TAX_CONCIERGE_A2UI_VERSION = "0.9.1"
TAX_CONCIERGE_CATALOG_ID = "https://tax-concierge.local/catalogs/v1/tax-concierge.json"

A2UIMessageKind = Literal[
    "createSurface",
    "updateDataModel",
    "updateComponents",
    "deleteSurface",
]


class A2UIBinding(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str


class A2UIAction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event: str
    payload: dict[str, Any] = Field(default_factory=dict)


class A2UIComponent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    component: str
    props: dict[str, Any] = Field(default_factory=dict)
    children: list[str] = Field(default_factory=list)
    binding: A2UIBinding | None = None
    action: A2UIAction | None = None


class A2UIMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: str = TAX_CONCIERGE_A2UI_VERSION
    message: A2UIMessageKind
    surface_id: str = Field(alias="surfaceId")
    catalog_id: str = Field(default=TAX_CONCIERGE_CATALOG_ID, alias="catalogId")
    root: str | None = None
    data: dict[str, Any] | None = None
    components: list[A2UIComponent] | None = None


class UploadedDocument(BaseModel):
    name: str
    document_type: str | None = None
    content: str | None = None
    ocr_text: str | None = None
    extracted_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaxIntake(BaseModel):
    user_story: str
    uploaded_documents: list[UploadedDocument] = Field(default_factory=list)
    known_facts: dict[str, Any] = Field(default_factory=dict)
    missing_facts: list[str] = Field(default_factory=list)
    candidate_entities: list[EntityPath] = Field(
        default_factory=lambda: ["Cannot Determine Yet"]
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    a2ui_messages: list[A2UIMessage] = Field(default_factory=list)
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
