from graphviz import Digraph
import textwrap
import tempfile
import os
import time
import uuid
import webbrowser

# Configurando as cores usadas no diagrama:
node_style = {
    "sequence": {
        "fillcolor": "#CACACA",
        "color":     "#000000",
        "fontcolor": "#080E16",
        "symbol":    "→",
        "label":     "SEQUENCE",
    },
    "fallback": {
        "fillcolor": "#70AA99",
        "color":     "#194D41",
        "fontcolor": "#050301",
        "symbol":    "?",
        "label":     "FALLBACK",
    },
    "condition": {
        "fillcolor": "#B1A696",
        "color":     "#5E4C28",
        "fontcolor": "#020201",
        "symbol":    "◆",
        "label":     "CONDITION",
    },
    "agent": {
        "fillcolor": "#B6CFD3",
        "color":     "#375874",
        "fontcolor": "#020805",
        "symbol":    "▶",
        "label":     "AGENT",
    },
}

default_style = {
    "fillcolor": "#F0F0F0",
    "color":     "#888888",
    "fontcolor": "#333333",
    "symbol":    "•",
    "label":     "NODE",
}


def format_text(text, width=22):
    if text is None:
        return "null"
    return "\n".join(textwrap.wrap(str(text), width))

def _build_label(node: dict) -> str:
    node_type = node.get("type", "unknown").lower()
    style = node_style.get(node_type, default_style)
    symbol  = style["symbol"]
    type_lbl = style["label"]
    lines = [f"{symbol}  {type_lbl}"]
    lines.append("─" * 18)

    content = format_text(node.get("content"), width=24)
    if content and content != "null":
        lines.append(content)
        lines.append("─" * 18)
 
    field_map = {
        "skill_id":        "Skill",
        "detector_key":    "Detector",
        "expected_zone":   "Zone",
        "expected_value":  "Expected",
        "evaluation_mode": "Eval mode",
        "field":           "Field",
    }
    details = []
    for key, display in field_map.items():
        val = node.get(key)
        if val is not None and str(val).strip() not in ("", "None", "null", "default_field"):
            wrapped = format_text(val, width=20)
            details.append(f"{display}: {wrapped}")
 
    if details:
        lines.extend(details)
 
    return "\\n".join(lines)
 
 
def _add_node(dot: Digraph, node: dict, parent_id: str | None, counter: list) -> None:
    node_id = f"n{counter[0]}"
    counter[0] += 1
 
    node_type = node.get("type", "unknown").lower()
    style = node_style.get(node_type, default_style)
    label = _build_label(node)
 
    is_leaf = not node.get("children")
    shape = "box"
    penwidth = "1.2" if is_leaf else "2.0"
 
    dot.node(
        node_id,
        label=label,
        shape=shape,
        style="filled,rounded",
        fillcolor=style["fillcolor"],
        color=style["color"],
        fontcolor=style["fontcolor"],
        fontname="Helvetica Neue",
        fontsize="11",
        margin="0.18,0.12",
        penwidth=penwidth,
    )
 
    if parent_id is not None:
        dot.edge(
            f"{parent_id}:s", 
            f"{node_id}:n",
            color="#272829",
            arrowhead="vee",
            arrowsize="0.8",
            penwidth="1.5",
            splines="curved",
        )
 
    for child in node.get("children", []):
        _add_node(dot, child, node_id, counter)


def render_behavior_tree(bt: dict, fmt: str = "svg") -> None:
    """
    Renderiza e exibe uma Behavior Tree temporariamente usando o navegador de internet.
    """
    bt_name  = bt.get("bt_name",  "behavior_tree")
    agent_id = bt.get("agent_id", "")
    title    = f"{bt_name}  |  agent: {agent_id}" if agent_id else bt_name
 
    dot = Digraph(name=bt_name, comment=title)
    dot.attr(
        rankdir="TB",
        splines="polyline", 
        nodesep="0.55",
        ranksep="0.75",
        bgcolor="white",
        fontname="Helvetica Neue",
        fontsize="13",
        label=title,
        labelloc="t",
        pad="0.4",
    )
    dot.attr("graph", margin="0.3")
 
    root = bt.get("root")
    if root is None:
        raise ValueError("O dicionário 'bt' não contém a chave 'root'.")
 
    _add_node(dot, root, parent_id=None, counter=[0])
 
    current_dir = os.getcwd()
    codigo_unico = uuid.uuid4().hex[:6]
    file_base_name = f".bt_temp_{bt_name}_{codigo_unico}"
    output_path = os.path.join(current_dir, file_base_name)
    
    dot.render(output_path, format=fmt, view=False, cleanup=True)
    
    final_file = f"{output_path}.{fmt}"
    
    webbrowser.open(f"file://{final_file}")
    
    print(f"Diagrama gerado! Arquivo (oculto) criado em: {final_file}")