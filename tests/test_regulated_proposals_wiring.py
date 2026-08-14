import ast
from pathlib import Path


SOURCE = Path("custom_components/frakon_energy/__init__.py")


def _tree() -> ast.Module:
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def _async_function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Missing async function {name}")


def _call_lines(node: ast.AST, name: str) -> list[int]:
    lines = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name) and func.id == name:
            lines.append(child.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == name:
            lines.append(child.lineno)
    return sorted(lines)


def test_regulated_proposal_websocket_is_imported_once() -> None:
    tree = _tree()
    imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "regulated_proposals_ws_api"
    ]
    assert len(imports) == 1
    assert [alias.name for alias in imports[0].names] == [
        "async_register_regulated_tariff_proposals_websocket"
    ]


def test_regulated_confirmation_authority_is_registered_after_read_only_all_in_preview() -> None:
    setup = _async_function(_tree(), "async_setup_entry")
    all_in = _call_lines(setup, "async_register_tariff_all_in_preview_websocket")
    regulated = _call_lines(setup, "async_register_regulated_tariff_proposals_websocket")
    load_plan = _call_lines(setup, "async_register_load_plan_websocket")

    assert len(all_in) == len(regulated) == len(load_plan) == 1
    assert all_in[0] < regulated[0] < load_plan[0]
