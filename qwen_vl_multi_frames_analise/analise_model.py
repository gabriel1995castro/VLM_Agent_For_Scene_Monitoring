from pydantic import BaseModel, Field
from typing import Literal


class TemporalVmlModel(BaseModel):
    
    scene_type: Literal["static", "human_action", "object_change", "mixed"] = Field(
        description=(
            "Classify the dominant type of change observed. "
            "'static' if nothing changed, 'human_action' for body/limb movement, "
            "'object_change' for objects appearing/disappearing/moving, "
            "'mixed' for both."
        )
    )
    
    change_detected: bool = Field(
        description="True only if a meaningful visual change occurred across the sequence.")
    
    confidence: Literal["low", "medium", "high"] = Field(
        description=(
            "Your confidence in the description. "
            "Use 'low' when frames are blurry, too similar, or ambiguous."
        )
    )
    description: str = Field(
        description=(
            "In Brazilian Portuguese: describe ONLY the changes observed. "
            "If change_detected is False, write exactly: "
            "'Nenhuma mudança temporal significativa detectada.' "
            "Do NOT invent actions. Do NOT describe the static scene."
        )
    )
 
