from pydantic import BaseModel, Field
from typing import Literal

# 
class TemporalVmlModel(BaseModel):
    
    condition_met: bool = Field(
        description="True ONLY if the specific target condition requested by the user occurred across the sequence."
    )
    
    confidence: Literal["low", "medium", "high"] = Field(
        description=(
            "Your confidence in the evaluation. "
            "Use 'low' when frames are blurry, ambiguous, or the action is unclear."
        )
    )
    
    description: str = Field(
        description=(
            "In Brazilian Portuguese: briefly describe what actually happened in the video "
            "and explain WHY the target condition was marked as true or false."
        )
    )
 
