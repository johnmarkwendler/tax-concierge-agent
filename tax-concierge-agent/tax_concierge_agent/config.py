from pydantic import BaseModel, Field


class TaxWorkflowConfig(BaseModel):
    model_name: str = "gemini-3.1-flash-lite"
    confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    max_missing_facts: int = Field(default=3, ge=1)


CONFIG = TaxWorkflowConfig()
