import asyncio
import json
import yaml
import time
import logging
from src.schemas.vision_agent_schema import SceneDescription
from src.agents.vision_agent import describe_scene
from src.agents.verificator_agent import run_decision_agent
from src.schemas.plan_agents import BTProposal
from src.agents.plan_agents import Plan_agent_1, Plan_agent_2
from src.agents.verificator_agent import Decision_agent
from src.agents.repair_agents_reponse import  parse_bt_proposal
from src.schemas.plan_agents import PlanId
from src.utils.print_bt_generate import render_behavior_tree

logger = logging.getLogger(__name__)


GUIDANCE_KEYWORDS = ("leva", "guia", "acompanha", "me leve", "me guie", "ir para", "vá até", "leve-me", "guie-me")

def is_guindance_request(task_desc:str) -> bool:
    return any(kw in task_desc.lower() for kw in GUIDANCE_KEYWORDS)

def build_navigation_context(scene:SceneDescription, task_desc:str) -> str:
    """
    Analise a cena e gera uma instrucao sobre navegacao para auxiliar o modelo de planejamento de BTs.
    """

    if is_guindance_request(task_desc):
        return (""
            "## Navigation Pre-Analysis (USE THIS TO DECIDE IF move_to IS NEEDED)\n"
            "USER EXPLICITLY REQUESTED GUIDANCE/NAVIGATION. "
            "Generate move_to as the primary action."
        "")
    
    foreground_objects = [
        obj.name  for obj in scene.objects
        if obj.zone.value == "foreground" and obj.is_reachable
    ]

    person_in_foureground = any (
        p.zone.value == "foreground" for p in scene.persons
    )

    task_lower = task_desc.lower()
    # visible_relevante_obj = [
    #     obj.name for obj in scene.objects
    #     if any (word in obj.name.lower() for word in task_lower.split())
    #     and obj.zone.value in ("foreground", "midground")
    # ]
    observed_objects = [obj.name for obj in scene.objects]
    lines = ["## Navigation Pre-Analysis (USE THIS TO DECIDE IF move_to IS NEEDED)"]
    lines.append(
    f"\n## Observed Objects (VLM Ground Truth)\n"
    f"ONLY these objects were actually seen: {', '.join(observed_objects)}\n"
    "CRITICAL: detector_key MUST reference ONLY objects from this list.\n"
    "If target object is NOT here, MUST use scan_environment first.")
    

    # if not visible_relevante_obj :
    #     lines.append(
    #         "\n TARGET OBJECT NOT VISIBLE IN CURRENT SCENE.\n"
    #         "The object requested by the user is NOT detected in any zone.\n"
    #         "Generate a plan that FIRST navigates to find the object "
    #         "(scan_environment or move_to a likely location), "
    #         "THEN detects it. Do NOT skip navigation in this case."
    #     )
    observed_names = [obj.name for obj in scene.objects]
    lines.append(f"\n## VLM Observed Objects (Ground Truth)\n"
        f"Visible: {', '.join(observed_names) if observed_names else 'none'}\n"
        "detector_key MUST reference ONLY these objects or their canonical forms.\n"
        "If the target object is NOT in this list, you MUST follow the SEMANTIC NAVIGATION HEURISTIC below.")
   
    # modifica o prompt de planejamento de acordo com o região onde objeto se encontra:
    if foreground_objects:
        lines.append(f"OBJECTS ALREADY IN FOREGROUND (reachable): {', '.join(foreground_objects)}")
        lines.append(
            "The robot is already near the workspace. "
            "DO NOT generate move_to. Start directly with detect_object or check_state."
        )
    
    else:
        lines.append("NO reachable objects in foreground zone.")
        lines.append(
            "The robot needs to navigate. "
            "Generate move_to wrapped in a fallback with replan_route."
        )
    
    lines.append("\n## CRITICAL SEMANTIC NAVIGATION RULE (REQUIRED)"
            "\nIf the target object is NOT in the VLM list, you MUST use an Anchor Object (e.g., desk, table, backpack) that IS in the list."
            "\nYour generated JSON MUST contain this exact Sequence structure:"
            "\n{""\n  \"type\": \"sequence\",""\n  \"content\": \"Navegar e procurar\",""\n  \"children\": ["
            "\n    {\"type\": \"agent\", \"skill_id\": \"move_to\", \"content\": \"Mover para [ANCHOR OBJECT NAME HERE]\"},"
            "\n    {\"type\": \"agent\", \"skill_id\": \"scan_environment\", \"content\": \"Escanear o ambiente\"},"
            "\n    {\"type\": \"agent\", \"skill_id\": \"detect_object\", \"content\": \"Detectar [TARGET NAME HERE]\"}"
            "\n  ]""\n}""\nAny plan for non-visible objects that does not start with 'move_to' will be REJECTED.")
    
    #Modifica o prompt dinamicamente para considerar a posicao da pessoa na imagem:
    if person_in_foureground:
         lines.append("PERSON is in foreground - human operator is already at the workspace.")

    lines.append(
    "\n## Task Scope Warning\n"
    f"ONLY plan for ONE primary object related to: '{task_desc}'\n"
    "If the task mentions alternatives ('cup OR bottle'), pick the FIRST or MOST VISIBLE one.\n"
    "DO NOT generate separate action sequences for each alternative.\n"
    "DO NOT add actions for any other visible objects not mentioned in the task.")
    
    return "\n".join(lines)

async def generate_bt_llm(task_desc: str, frame_b64: str | None = None, skills_context: str ="") -> BTProposal:
    """Gera dois planos de BT e seleciona o melhor via Decision_agent."""
    total_start = time.perf_counter()
    parse_start = time.perf_counter()
    print("Inferindo contexto utilizando o VLM...")
    if frame_b64 is None:
        image_path = "src/1000017921.jpg"
        scene: SceneDescription = await describe_scene(image_path=image_path)
    else:
        scene: SceneDescription = await describe_scene(frame_b64=frame_b64)
        
    scene.persons = [p for p in scene.persons if p.position != "unknown"]
    scene_context = scene.to_prompt_context()
    nav_context = build_navigation_context(scene, task_desc)
    # user_message = f"""## Task
    #                 {task_desc}
    #                 {nav_context}
    #                 {skills_context}
    #                 {scene_context}"""
    user_message = (
    f"## Perceptual Ground Truth (VLM — PRIMARY SOURCE)\n"
    f"{scene_context}\n\n"
    f"## Task\n{task_desc}\n\n"
    f"## Navigation Pre-Analysis\n{nav_context}\n\n"
    f"## Available Skills\n{skills_context}")

    parse_end = time.perf_counter()
    
    # Gera dois planos em paralelo com agents independentes
    generation_start = time.perf_counter()
    bt_agent_1 = Plan_agent_1.run(f"Plan A:\n{user_message}")
    bt_agent_2 = Plan_agent_2.run(f"Plan B:\n{user_message}")
    bt_1_raw, bt_2_raw = await asyncio.gather(bt_agent_1, bt_agent_2)
    generation_end = time.perf_counter()
    print(f"\nTempo geração paralela: "
        f"{generation_end - generation_start:.2f} segundos")
    
    parse_start = time.perf_counter()
    bt_1 = parse_bt_proposal(bt_1_raw.output)
    bt_2 = parse_bt_proposal(bt_2_raw.output)
    parse_end = time.perf_counter()
    print(f"Tempo parsing: "
          f"{parse_end - parse_start:.2f} segundos")
   
    print("")

    observed_for_judge = [obj.name for obj in scene.objects]
    validation_input = (
        f"SCENE CONTEXT (VLM — PRIMARY SOURCE OF TRUTH):\n{scene_context}\n\n"
        f"OBSERVED OBJECTS (GROUNDING CHECK): {', '.join(observed_for_judge)}\n"
        f"CRITICAL RULES FOR UNSEEN OBJECTS:\n"
        f"If the target is NOT in the observed list, the plan MUST use a semantic anchor (e.g., table, door) that IS in the list.\n"
        f"The correct sequence is: 1. move_to(anchor), 2. scan_environment, 3. detect_object(target).\n"
        f"Plans that use blind 'scan_environment' without moving to a logical anchor first should be penalized.\n\n"
        f"NAVIGATION CONTEXT:\n{nav_context}\n\n"
        f"NAVIGATION CONTEXT:\n{nav_context}\n\n"
        f"AVAILABLE SKILLS (YAML):\n{skills_context}\n\n"
        f"PLANO A:\n{bt_1.model_dump_json()}\n\n"
        f"PLANO B:\n{bt_2.model_dump_json()}"
    )
    print("============================================")
    print("BT Plano A:")
    print(bt_1.model_dump_json(indent=2))
    #render_behavior_tree(bt=bt_1.model_dump())
    print("============================================")
    print("BT Plano B:")
    print(bt_2.model_dump_json(indent=2))
    #render_behavior_tree(bt=bt_2.model_dump())
    print("============================================")

    judge_start = time.perf_counter()
    winner_output = await run_decision_agent(validation_input)
    print(f"\nTempo do juiz: {time.perf_counter() - judge_start:.2f} segundos")

    if winner_output.selected_plan_id is None:
        logger.warning("Validador não selecionou plano — usando plan_a como fallback")
        best_tree = bt_1
    else:
        best_tree = bt_1 if winner_output.selected_plan_id == PlanId.PLAN_A else bt_2
    
    best_tree.agent_id = "validador"
    print("============================================")
    print(f"Plano escolhido: {best_tree.agent_id}")
    print(f"Justificativa: {winner_output.justification}")
    print("============================================")
    print("BT escolhida:")
    print(best_tree.model_dump_json(indent=2))
    #render_behavior_tree(bt=best_tree.model_dump())
    
    print("============================================")
    print(f"\nTempo TOTAL pipeline: {time.perf_counter() - total_start:.2f} segundos")
    print("============================================")
    return best_tree