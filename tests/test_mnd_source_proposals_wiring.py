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


def test_mnd_source_proposal_websocket_is_imported_once() -> None:
    tree = _tree()
    imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "providers.mnd_source_proposals_ws_api"
    ]
    assert len(imports) == 1
    assert [alias.name for alias in imports[0].names] == [
        "async_register_mnd_source_proposals_websocket"
    ]


def test_mnd_source_authority_is_registered_after_customer_tariff_authority_before_load_execution() -> None:
    setup = _async_function(_tree(), "async_setup_entry")
    customer = _call_lines(setup, "async_register_customer_tariff_proposals_websocket")
    mnd_source = _call_lines(setup, "async_register_mnd_source_proposals_websocket")
    load_plan = _call_lines(setup, "async_register_load_plan_websocket")

    assert len(customer) == len(mnd_source) == len(load_plan) == 1
    assert customer[0] < mnd_source[0] < load_plan[0]
