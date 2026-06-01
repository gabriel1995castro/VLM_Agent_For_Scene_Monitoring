from pydantic import BaseModel, Field
from typing import Optional
from src.schemas.plan_agents import PlanId


class BTFinalDecision(BaseModel):
    selected_plan_id: PlanId = Field(
        ...,
        description="ID of the winning plan. Must be exactly 'plan_a' or 'plan_b'."
    )
    justification: str = Field(
        ...,
        description="Technical justification referencing specific structural or semantic rules."
    )
    suggested_refinements: Optional[str] = Field(
        None,
        description="Minimal concrete fixes for the rejected plan. Null if none needed."
    )
    is_safe_to_execute: bool = Field(
        True,
        description="Whether the selected plan is safe to send to the robot executor."
    )