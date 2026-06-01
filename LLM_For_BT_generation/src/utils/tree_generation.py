from __future__ import annotations

import json
import logging
from typing import Any

from src.utils.tree_planner import (
    TaskTree,
    ControlFlowNode,
    AgentNode,
    TreeConfig,
    TreeNode,
)

from src.schemas.plan_agents import BTProposal

logger = logging.getLogger("uvicorn.error")

class ObserverNode(TreeNode):
    """Observa o detector e extrai valores para evaluation."""

    def __init__(self, cfg: TreeConfig, detector_key: str, depth: int):
        super().__init__(cfg, detector_key, depth)
        self.field: str = "default_field"
        self.expected_value: Any = None
        self.ignored_values: list[Any] = []

    async def run(self,step_id: int,decision_id: int,log: logging.Logger,traj_d: dict[str, Any] | None = None,) -> dict[str, Any]:
        """Extrai valor do vision_result e armazena para condition check."""
        log.info("session=%s ObserverNode waiting for vision: detector=%s field=%s",
                 traj_d.get("session_id") if traj_d else "unknown", self.content, self.field)

        # Esperar resultado do VLM via session queue
        try:
            
            import asyncio

            if traj_d and "result" in traj_d:
                result = traj_d["result"]
                value = extract_result_value(result, self.field)
                log.info("session=%s ObserverNode value=%s", traj_d.get("session_id"), value)

                if value in self.ignored_values:
                    return {
                        "success": False,
                        "is_ignored": True,
                        "step_id": step_id,
                        "decision_id": decision_id,
                    }

                # Sucesso se tem valor válido
                traj_d["observer_value"] = value
                return {
                    "success": True,
                    "step_id": step_id,
                    "decision_id": decision_id,
                }

            # Caso contrário, esperar do queue
            event = await self._wait_for_vlm_result()
            result = event.payload.get("result", {})
            value = extract_result_value(result, self.field)

            if value in self.ignored_values:
                return {
                    "success": False,
                    "is_ignored": True,
                    "step_id": step_id,
                    "decision_id": decision_id,
                }

            traj_d["observer_value"] = value
            return {
                "success": True,
                "step_id": step_id,
                "decision_id": decision_id,
            }

        except Exception as e:
            log.error("session=%s ObserverNode error: %s",traj_d.get("session_id") if traj_d else "unknown", e)
            return {
                "success": False,
                "step_id": step_id,
                "decision_id": decision_id,
            }

    async def _wait_for_vlm_result(self) -> Any:
        raise NotImplementedError("ObserverNode must be used with session queue")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "observer",
            "content": self.content,
            "depth": self.depth,
            "field": self.field,
            "expected_value": self.expected_value,
            "ignored_values": self.ignored_values,
        }


class ConditionNode(TreeNode):
    """Verifica a condition para completar o step."""
    def __init__(self, cfg: TreeConfig, condition: dict[str, Any], depth: int):
        super().__init__(cfg, json.dumps(condition) if isinstance(condition, dict) else str(condition), depth)
        self.condition: dict[str, Any] = condition if isinstance(condition, dict) else {"condition": condition}
        self.evaluation_mode: str = "match_value"

    async def run(self,step_id: int,decision_id: int,log: logging.Logger,traj_d: dict[str, Any] | None = None,) -> dict[str, Any]:
        
        value = traj_d.get("observer_value") if traj_d else None
        log.info("session=%s ConditionNode checking: condition=%s value=%s",
                 traj_d.get("session_id") if traj_d else "unknown", self.condition, value)

        try:
          
            if self.evaluation_mode == "match_value":
                expected = self.condition.get("expected_value")
                success = value == expected

            elif self.evaluation_mode == "numeric_threshold":
                threshold = self.condition.get("threshold", 0)
                op = self.condition.get("operator", ">=")
                success = self._compare_numeric(value, op, threshold)

            elif self.evaluation_mode == "boolean_true":
                success = bool(value) is True

            elif self.evaluation_mode == "enum_value":
                expected = self.condition.get("expected_value")
                success = str(value) == str(expected)

            else:
             
                expected = self.condition.get("expected_value")
                success = value == expected

            log.info("session=%s ConditionNode result: %s",traj_d.get("session_id") if traj_d else "unknown", success)

            return {
                "success": success,
                "step_id": step_id, 
                "decision_id": decision_id,
            }

        except Exception as e:
            log.error("session=%s ConditionNode error: %s",traj_d.get("session_id") if traj_d else "unknown", e)
            return {
                "success": False,
                "step_id": step_id,
                "decision_id": decision_id,
            }

    def _compare_numeric(self, value: Any, op: str, threshold: float) -> bool:
        """Compara valor numérico com threshold."""
        try:
            num_value = float(value) if value is not None else 0
            if op == ">=":
                return num_value >= threshold
            elif op == ">":
                return num_value > threshold
            elif op == "<=":
                return num_value <= threshold
            elif op == "<":
                return num_value < threshold
            elif op == "==":
                return num_value == threshold
            return False
        except (ValueError, TypeError):
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "condition",
            "content": self.content,
            "depth": self.depth,
            "condition": self.condition,
            "evaluation_mode": self.evaluation_mode,
        }


def extract_result_value(result: dict[str, Any], field: str) -> Any:
    """Extrai valor do resultado do VLM baseado no field/path."""
    if not result or not field:
        return None

    # Suporte a caminhos como "a.b.c"
    parts = field.split(".")
    value = result

    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None

    return value


def convert_bt_node_to_tasktree(node_def: dict[str, Any], cfg: TreeConfig, depth: int = 0, session: Any = None, manager: Any = None) -> TreeNode:
    """
    Converte BTNodeDefinitions (schema do LLM) para TreeNode executável.

    Args:
        node_def: Dicionário com estrutura BTNodeDefinitions
        cfg: TreeConfig
        depth: Profundidade atual na árvore
        session: Session para AgentNode
        manager: Manager para AgentNode

    Returns:
        TreeNode: Nó da árvore (ControlFlowNode, AgentNode, ObserverNode, ou ConditionNode)
    """
    node_type = node_def.get("type", node_def.get("node_type", "")).upper()
    content = node_def.get("content", "")

    # Mapeia node_type para classes
    if node_type == "SEQUENCE":
        node = ControlFlowNode(cfg, "sequence", depth=depth)
    elif node_type == "FALLBACK":
        node = ControlFlowNode(cfg, "fallback", depth=depth)
    elif node_type == "PARALLEL":
        node = ControlFlowNode(cfg, "parallel", depth=depth)
    elif node_type in ("AGENT", "ACTION"):
        skill_id = node_def.get("skill_id")
        node = AgentNode(cfg, content, depth=depth, session=session, manager=manager, skill_id=skill_id)
    elif node_type == "OBSERVER":
        node = ObserverNode(cfg, content, depth=depth)
        # Configura campos adicionais do observer
        if "field" in node_def:
            node.field = node_def["field"]
        if "expected_value" in node_def:
            node.expected_value = node_def["expected_value"]
        if "ignored_values" in node_def:
            node.ignored_values = node_def["ignored_values"]
    elif node_type == "CONDITION":
        cond_dict = node_def.get("condition", {})
        node = ConditionNode(cfg, cond_dict, depth=depth)
        if "evaluation_mode" in node_def:
            node.evaluation_mode = node_def["evaluation_mode"]
    else:
        # Default: trata como sequence
        node = ControlFlowNode(cfg, "sequence", depth=depth)

    # Adiciona filhos recursivamente
    children = node_def.get("children", [])
    for child_def in children:
        child_node = convert_bt_node_to_tasktree(child_def, cfg, depth + 1, session, manager)
        node.add_child(child_node)

    return node


async def generate_tasktree_from_bt_proposal(bt_proposal: BTProposal, cfg: TreeConfig, session: Any = None, manager: Any = None) -> TaskTree:
    """
    Gera uma TaskTree a partir de uma BTProposal (resultado do Plan_agent).

    Args:
        bt_proposal: BTProposal do Plan_agent
        cfg: TreeConfig
        session: Session para AgentNode
        manager: Manager para AgentNode

    Returns:
        TaskTree: Árvore executável
    """
    # Converte o root node para TaskTree
    root_node = convert_bt_node_to_tasktree(bt_proposal.root.model_dump(), cfg, depth=0, session=session, manager=manager)

    return TaskTree(cfg, root_node, step_id=1, session=session)