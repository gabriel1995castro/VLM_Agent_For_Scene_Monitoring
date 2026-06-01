from __future__ import annotations

import asyncio
import dataclasses
import uuid
from typing import Any
import logging

logger = logging.getLogger("uvicorn.error")


@dataclasses.dataclass
class TreeConfig:
    max_depth: int = 10
    max_attempts_before_expand: int = 5
    max_substeps_on_expand: int = 5
    expand_in_place: bool = True
    max_expansions_per_node: int = 1 
    max_expansion_depth: int = 3


class TreeNode:
    """Node base para behavior tree."""

    def __init__(self, cfg: TreeConfig, content: str, depth: int):
        
        self.cfg = cfg
        self.content = content
        self.depth = depth
        self.children: list[TreeNode] = []
        self.parent: TreeNode | None = None
        self.attempts: int = 0
        self.expand_count: int = 0    
        self.id = str(uuid.uuid4())                          
        self.max_expansions: int = cfg.max_expansions_per_node  
    
    def get_skill_id(self) -> str | None:
        return None

    def get_observation_key(self) -> str | None:
        return None


    def add_child(self, child_node: TreeNode) -> None:
        child_node.parent = self
        self.children.append(child_node)

    async def run(
        self,
        step_id: int,
        decision_id: int,
        log: logging.Logger,
        traj_d: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError()


class ControlFlowNode(TreeNode):
    """Node de controle de fluxo: sequence, fallback, parallel."""

    async def run(
        self,
        step_id: int,
        decision_id: int,
        log: logging.Logger,
        traj_d: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.depth > self.cfg.max_depth:
            log.info("Max depth reached")
            return {
                "success": False,
                "terminate": "max_depth",
                "step_id": step_id,
                "decision_id": decision_id,
            }

        if self.content == "sequence":
            return await self._run_sequence(step_id, decision_id, log, traj_d)
        elif self.content == "fallback":
            return await self._run_fallback(step_id, decision_id, log, traj_d)
        elif self.content == "parallel":
            return await self._run_parallel(step_id, decision_id, log, traj_d)
        else:
            raise NotImplementedError(f"Unknown control flow: {self.content}")

    async def _run_sequence(
        self,
        step_id: int,
        decision_id: int,
        log: logging.Logger,
        traj_d: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for child in self.children:
            result = await child.run(step_id, decision_id, log, traj_d)
            step_id, decision_id = result["step_id"], result["decision_id"]
            if not result["success"]:
                return {"success": False, "step_id": step_id, "decision_id": decision_id}
        return {"success": True, "step_id": step_id, "decision_id": decision_id}

    async def _run_fallback(
        self,
        step_id: int,
        decision_id: int,
        log: logging.Logger,
        traj_d: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for child in self.children:
            result = await child.run(step_id, decision_id, log, traj_d)
            step_id, decision_id = result["step_id"], result["decision_id"]
            if result["success"]:
                return {"success": True, "step_id": step_id, "decision_id": decision_id}
        return {"success": False, "step_id": step_id, "decision_id": decision_id}

    async def _run_parallel(
        self,
        step_id: int,
        decision_id: int,
        log: logging.Logger,
        traj_d: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tasks = [child.run(step_id, decision_id, log, traj_d) for child in self.children]
        results = await asyncio.gather(*tasks)

        is_success = True
        for result in results:
            if not result["success"]:
                is_success = False
            step_id, decision_id = result["step_id"], result["decision_id"]

        return {"success": is_success, "step_id": step_id, "decision_id": decision_id}

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "control_flow",
            "content": self.content,
            "depth": self.depth,
            "children": [child.to_dict() for child in self.children],
        }


class AgentNode(TreeNode):
    """Node de ação que executa tarefas e espera confirmação do VLM."""

    def __init__(
        self,
        cfg: TreeConfig,
        content: str,
        depth: int,
        session: Any = None,
        manager: Any = None,
        skill_id: str | None = None,
        parameters: dict[str, Any] | None = None,
    ):
        super().__init__(cfg, content, depth)
        self.session = session
        self.manager = manager
        self.expand_count = 0
        self.attempts = 0
        self.skill_id: str | None = skill_id
        self.parameters: dict[str, Any] = parameters or {}
    
    def get_skill_id(self):
        return self.skill_id
    
    async def run(
        self,
        step_id: int,
        decision_id: int,
        log: logging.Logger,
        traj_d: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        
        completed = await self._wait_for_vlm_confirmation(log)

        if completed:
            return {
                "success": True,
                "step_id": step_id,  
                "decision_id": decision_id,
            }

        self.attempts += 1
        if self.attempts >= self.cfg.max_attempts_before_expand:
            return {
                "success": False,
                "needs_expansion": True,
                "step_id": step_id,
                "decision_id": decision_id,
            }

        return {
            "success": False,
            "needs_expansion": False,
            "step_id": step_id,
            "decision_id": decision_id,
        }

    async def _wait_for_vlm_confirmation(
        self, log: logging.Logger
    ) -> bool:
        try:
            event = await self.session.queue.get()

            if event.kind == "overshoot.result":
                result = event.payload.get("result", {})
                flag = bool(result.get("flag", False))
                log.info(
                    "session=%s AgentNode VLM confirmation: %s",
                    self.session.session_id,
                    flag,
                )
                return flag

            await self.session.queue.put(event)
            return False
        except Exception as e:
            log.error("session=%s VLM confirmation error: %s", self.session.session_id, e)
            return False

    async def retry(self) -> bool:
        self.attempts = 0
        return await self._wait_for_vlm_confirmation(logger)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "agent",
            "content": self.content,
            "skill_id": self.skill_id,
            "depth": self.depth,
            "attempts": self.attempts,
        }


class TaskTree:
    """Behavior tree completa para uma tarefa."""

    def __init__(self,cfg: TreeConfig,root: TreeNode,step_id: str | int = 0,session: Any = None,):
        
        self.cfg = cfg
        self.root = root
        self.current_node: TreeNode | None = root
        self.current_node_index: int = 0
        self.step_id: str | int = step_id
        self.decision_id: int = 0
        self.session = session
        self.selected_action: dict[str, Any] | None = None
        self.failed: bool = False
        self.failed_node: Any = None
        self.vision_result: dict[str, Any] | None = None
        self.config = cfg
        

    async def tick(self) -> bool:
        """
        Executa um tick na behavior tree.
        Retorna True se alguma ação foi selecionada para execução.
        """
        log = logging.getLogger("uvicorn.error")

        traj_d = {
            "session_id": self.session.session_id,
            "result": self.vision_result or {},
        }

        try:
            result = await self.current_node.run(
                self.step_id, self.decision_id, log, traj_d
            )

            # Processar resultado
            self.step_id = result.get("step_id", self.step_id)
            self.decision_id = result.get("decision_id", self.decision_id)

            if result.get("success"):
                # Sucesso - selecionar ação de progresso
                self.selected_action = {
                    "name": "progress",
                    "step_id": None,  # next_step_id será definido pelo workflow
                    "speech": "",
                }
                self.failed = False
                self.failed_node = None
                return True

            elif result.get("needs_expansion"):
                # Falha que requer expansão
                self.failed = True
                self.failed_node = result.get("failed_node")
                return False

            elif result.get("is_ignored"):
                # Valor ignorado - não falha, apenas continua
                self.failed = False
                self.failed_node = None
                return False

            else:
                # Falha normal
                self.failed = True
                self.failed_node = None
                return False

        except Exception as e:
            log.error("session=%s TaskTree tick error: %s",
                     traj_d.get("session_id"), e)
            self.failed = True
            return False

    async def execute(
        self, log: logging.Logger, traj_d: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        
        return await self.root.run(
            self.step_id, self.decision_id, log, traj_d
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root.to_dict(),
            "step_id": self.step_id,
            "decision_id": self.decision_id,
        }

    def refresh(self) -> None:
        """
        Reindexa a tree após expansão dinâmica.
        Reseta o índice atual para garantir consistência.
        """
        self.current_node_index = 0
        self.current_node = self.root






async def build_tree(task: str,inventory_items: str,person_state: str,scene_description: str,ollama_http: str,
    ollama_llm_model: str,session: Any = None,manager: Any = None,) -> TaskTree:
    
    """
    Usa o LLM para construir dinamicamente uma behavior tree a partir de um texto.

    Envia um prompt ao LLM solicitando uma decomposição em passos executáveis,
    e retorna uma TaskTree com sequence de AgentNodes.
    """
    import json

    # Prompt para o LLM decompor a tarefa
    prompt = f"""
    You are an assistant that plans physical tasks. Decompose the task below into executable steps.

    # IMPORTANT: All steps inside the JSON must be written in PORTUGUESE (BRAZIL).

    Task: {task}
    Available items/tools: {inventory_items}
    Person state: {person_state}
    Scene description: {scene_description}

    Return ONLY a valid JSON with the following structure (no markdown, no explanations):
    {{
    "steps": [
        "passo 1 dehttpxscritivo em português",
        "passo 2 descritivo em português",
        "passo 3 descritivo em português"
    ]
    }}

    Each step must be:
    - A specific and executable action
    - Clear and concise
    - Sequential (logical order of execution)
    """

    # Chama o LLM via Ollama
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{ollama_http}/api/generate",
            json={
                "model": ollama_llm_model,
                "prompt": prompt,
                "stream": False,
            },
            timeout=60.0,
        )

        result = response.json()
        response_text = result.get("response", "")

    # Extrai o JSON da resposta 
    response_text = response_text.strip()
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    if response_text.startswith("```"):
        response_text = response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]
    response_text = response_text.strip()

    try:
        parsed = json.loads(response_text)
        step_texts = parsed.get("steps", [])
    except json.JSONDecodeError:
        logger.error("Failed to parse LLM response as JSON: %s", response_text)
        step_texts = [task]  

    
    cfg = TreeConfig()
    sequence_node = ControlFlowNode(cfg, "sequence", depth=0)

    for step_text in step_texts:
        agent_node = AgentNode(cfg, step_text, depth=1, session=session, manager=manager)
        sequence_node.add_child(agent_node)

    return TaskTree(cfg, sequence_node, session=session)