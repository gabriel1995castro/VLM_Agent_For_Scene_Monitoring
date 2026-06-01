from pydantic import BaseModel

class Substep(BaseModel):
    description: str
    skill_id: str = "wait_for_human_action"

class ExpandProposal(BaseModel):
    substeps: list[Substep]