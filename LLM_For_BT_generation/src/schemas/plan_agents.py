from enum import Enum
from typing import List, Optional, Any
from pydantic import BaseModel, Field, field_validator


class BT_Element(str, Enum):
    SEQUENCE  = "sequence"
    FALLBACK  = "fallback"
    PARALLEL  = "parallel"
    CONDITION = "condition"
    OBSERVER  = "observer"
    AGENT     = "agent"



class EvaluationMode(str, Enum):
    MATCH_VALUE  = "match_value"
    IS_PRESENT   = "is_present"
    IS_ABSENT    = "is_absent"
    GREATER_THAN = "greater_than"
    LESS_THAN    = "less_than"


class SkillId(str, Enum):
    MOVE_TO               = "move_to"
    STOP                  = "stop"
    REPLAN_ROUTE          = "replan_route"
    SCAN_ENVIRONMENT      = "scan_environment"
    DETECT_OBJECT         = "detect_object"
    CHECK_STATE           = "check_state"
    REPOSITION_FOR_VIEW   = "reposition_for_view"
    CHECK_PERSON_PRESENCE = "check_person_presence"
    WAIT_FOR_HUMAN_ACTION = "wait_for_human_action"
    VERIFY_PICK_UP        = "verify_pick_up"
    VERIFY_PLACE          = "verify_place"
    VERIFY_OPEN           = "verify_open"
    VERIFY_CLOSE          = "verify_close"
    VERIFY_PUSH_PULL      = "verify_push_pull"


class PlanId(str, Enum):
    PLAN_A = "plan_a"
    PLAN_B = "plan_b"


class BTNodeDefinitions(BaseModel):
    type: BT_Element
    content: str = Field(..., description="Human-readable description of the node")
    skill_id: Optional[SkillId] = Field(
        None,
        description="Skill to execute — only on agent leaf nodes. Must be null on all other types."
    )
    expected_zone: Optional[str] = Field(
        None,
        description="Zone filter for observer/condition nodes: foreground | midground | background"
    )
    detector_key: Optional[str] = Field(
        None,
        description="Key to look up in robot memory for condition nodes (e.g. 'object_detected')"
    )
    field: str = Field(
        "default_field",
        description="Field name for evaluation. Use 'default_field' when not applicable."
    )
    expected_value: Optional[str] = Field(
        None,
        description="Value to match against detector_key result (e.g. 'true', 'reachable')"
    )
    evaluation_mode: EvaluationMode = Field(
        EvaluationMode.MATCH_VALUE,
        description="How to compare the detected value against expected_value"
    )
    children: List['BTNodeDefinitions'] = Field(
        default_factory=list,
        description="Child nodes. Must be [] for agent and condition nodes."
    )

    @field_validator("skill_id")
    @classmethod
    def skill_id_only_on_agent(cls, v, info):
        node_type = info.data.get("type")
        if node_type == BT_Element.AGENT and v is None:
            raise ValueError("agent nodes MUST have a valid skill_id")
        return v

    @field_validator("children")
    @classmethod
    def leaf_nodes_have_no_children(cls, v, info):
        node_type = info.data.get("type")
        if node_type in (BT_Element.AGENT, BT_Element.CONDITION) and len(v) > 0:
            raise ValueError(f"'{node_type}' nodes must have children = []")
        return v

BTNodeDefinitions.model_rebuild()


class BTProposal(BaseModel):

    agent_id: PlanId = Field(..., description="Planner ID: 'plan_a' or 'plan_b'")
    bt_name: str
    rationale: str = Field(..., description="Justification for the BT strategy")
    root: BTNodeDefinitions