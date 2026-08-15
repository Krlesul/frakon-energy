import ast
from pathlib import Path


SOURCE = Path("custom_components/frakon_energy/__init__.py")


def _tree() -> ast.Module:
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def _setup(tree: ast.Module) -> ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_setup_entry":
            return node
    raise AssertionError("Missing async_setup_entry")


def _call_line(node: ast.AST, name: str) -> int:
    lines = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name) and func.id == name:
            lines.append(child.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == name:
            lines.append(child.lineno)
    assert len(lines) == 1, f"Expected exactly one call to {name}, got {lines}"
    return lines[0]


def test_manual_customer_tariff_websocket_is_imported_once() -> None:
    imports = [
        node
        for node in _tree().body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "manual_customer_tariff_ws_api"
    ]
    assert len(imports) == 1
    assert [alias.name for alias in imports[0].names] == [
        "async_register_manual_customer_tariff_websocket"
    ]


def test_manual_proposal_registration_reuses_common_confirmation_boundary() -> None:
    setup = _setup(_tree())
    common_customer = _call_line(
        setup, "async_register_customer_tariff_proposals_websocket"
    )
    manual = _call_line(setup, "async_register_manual_customer_tariff_websocket")
    mnd_source = _call_line(setup, "async_register_mnd_source_proposals_websocket")
    load_plan = _call_line(setup, "async_register_load_plan_websocket")

    assert common_customer < manual < mnd_source < load_plan
