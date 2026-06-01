from typing import Any
import json
import logging
import httpx
from src.utils.tree_planner import (TreeConfig,TreeNode,ControlFlowNode,AgentNode,TaskTree,)
from src.agents.expanse_agent import expand_agent, parse_expand_proposal
from src.utils.tree_generation import ObserverNode, ConditionNode

logger = logging.getLogger("uvicorn.error")

_INTERNAL_SKILLS = {"replan_route"}
def tree_to_recipe_steps(tree: TaskTree) -> list[dict[str, Any]]:
    """
    Converte uma TaskTree em uma lista de steps no formato de recipe steps.
    """
    steps = []

    def collect_nodes(node: TreeNode):
        if isinstance(node, AgentNode):
            skill = getattr(node, "skill_id", None)
            if hasattr(skill, "value"):
                skill = skill.value
            if skill not in _INTERNAL_SKILLS: 
                steps.append({
                    "description": node.content,
                    "node_ref": node,
                    "expected_value": node.content,
                })

        for child in node.children:
            collect_nodes(child)

    collect_nodes(tree.root)
    return steps


async def expand_node(tree: TaskTree,node: TreeNode,scene_description: str,
    person_state: str,session: Any,manager: Any, existing_tasks: list[str] | None = None,) -> bool:
    """
    Expande um TreeNode que falhou varias vezes, gerando sub-tarefas mais simples.
    A profundidade de expansão é relativa ao nó que falhou.
    """

    if not node or not tree:
        return False

    max_exp_depth = getattr(tree.cfg, 'max_expansion_depth', 2)

    if node.expand_count >= max_exp_depth:
        logger.warning(
            "Node '%s' atingiu max_expansion_depth=%d",
            node.content, max_exp_depth
        )
        return False

    if node.expand_count >= node.max_expansions:
        logger.warning(
            "Node '%s' atingiu limite de expansões (%d)",
            node.content, node.max_expansions
        )
        return False
    
    parent_skill = getattr(node, "skill_id", None)
    parent_params = getattr(node, "parameters", {})
    
    new_children = await _generate_subtasks(
        parent_task=node.content,
        scene=scene_description,
        state=person_state,
        cfg=tree.cfg,
        session=session,
        manager=manager,
        parent_depth=node.depth,
        skill_id=parent_skill,
        parameters=parent_params,
        existing_tasks=existing_tasks,
        tree_snapshot=tree.root.to_dict(),
        failed_node=node.to_dict(),)

    if not new_children:
        return False

    node.expand_count += 1

    sequence_node = ControlFlowNode(
        tree.cfg,
        node.content,
        depth=node.depth + 1
    )

    for child in new_children:
        sequence_node.add_child(child)
        child.attempts = 0

    parent = node.parent
    if not parent:
        logger.error("Node sem parent não pode ser expandido")
        return False

    replaced = False

    for i, child in enumerate(parent.children):
        if child is node:   
            parent.children[i] = sequence_node
            sequence_node.parent = parent
            replaced = True
            break

    if not replaced:
        logger.error(
            "Falha ao localizar node na árvore (provável mismatch de referência): %s",
            node.content
        )
        return False

    # reset estado da árvore após modificação
    tree.current_node = tree.root
    tree.current_node_index = 0

    return True


async def _generate_subtasks(
    parent_task: str,
    scene: str,
    state: str,
    cfg: TreeConfig,
    session: Any,
    manager: Any,
    parent_depth: int = 1,
    skill_id: str | None = None,
    parameters: dict[str, Any] | None = None,
    existing_tasks: list[str] | None = None,
    tree_snapshot: dict[str, Any] | None = None,
    failed_node: dict[str, Any] | None = None,
) -> list[AgentNode]:
    """
    Gera sub-tarefas para uma task dada usando LLM.
    """
    max_substeps = getattr(cfg, 'max_substeps_on_expand', 5)

    existing_block = ""
    if existing_tasks:
        formatted = "\n".join(f"  - {t}" for t in existing_tasks)
        existing_block = (
            f"\nTasks already in the tree (DO NOT repeat or rephrase these):\n"
            f"{formatted}\n"
        )
    
    tree_block = ""
    if tree_snapshot:
            tree_block = (
                "\nCurrent Behavior Tree Structure:\n"
                f"{json.dumps(tree_snapshot, indent=2, ensure_ascii=False)}\n"
            )

    failed_block = ""
    if failed_node:
            failed_block = (
                "\nFailed Node:\n"
                f"{json.dumps(failed_node, indent=2, ensure_ascii=False)}\n"
            )

    user_prompt = (
        f"Original task: {parent_task}\n"
        f"Current scene: {scene}\n"
        f"Current agent state: {state}\n"
        f"{existing_block}"
        f"{failed_block}"
        f"{tree_block}"
        f"Maximum sub-steps: {max_substeps}."
    )

    try:
        result = await expand_agent.run(user_prompt)
        proposal = parse_expand_proposal(result.output)
        wrapper_depth = parent_depth + 1
        return [
            AgentNode(
                cfg,
                content=s.description,
                depth=wrapper_depth + 1,
                session=session,
                manager=manager,
                skill_id=s.skill_id or skill_id,
                parameters=parameters or {},
            )
            for s in proposal.substeps[:max_substeps]
        ]
    except Exception as e:
        logger.error("expand_agent falhou: %s", e)
        return []