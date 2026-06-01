import asyncio
from src.utils.tree_planner import TreeConfig
from src.agents.plan_agents import Plan_agent_1, Plan_agent_2
from src.agents.verificator_agent import Decision_agent
from src.utils.tree_generation import generate_tasktree_from_bt_proposal

async def run_bt_pipeline(user_instruction, session, manager):
    cfg = TreeConfig()

    plan_a, plan_b = await asyncio.gather(
        Plan_agent_1.run(f"Instruction: {user_instruction}", deps=manager),
        Plan_agent_2.run(f"Instruction: {user_instruction}", deps=manager),
    )


    verdict = await Decision_agent.run(
        f"Choose the best one:\n"
        f"Plan A: {plan_a.output.model_dump_json()}\n"
        f"Plan B: {plan_b.output.model_dump_json()}"
    )
    winner_data = plan_a.output if verdict.output.selected_plan_id == "plan_a" else plan_b.output

    print(f"Vencedor: {winner_data.agent_id}. Justificativa: {verdict.output.justification}")

    final_tree = await generate_tasktree_from_bt_proposal(
        bt_proposal=winner_data,
        cfg=cfg,
        session=session,
        manager=manager,
    )

    return final_tree