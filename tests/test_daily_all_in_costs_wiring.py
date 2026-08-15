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


def test_daily_all_in_cost_websocket_is_imported_once() -> None:
    imports = [
        node
        for node in _tree().body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "daily_all_in_costs_ws_api"
    ]
    assert len(imports) == 1
    assert [alias.name for alias in imports[0].names] == [
        "async_register_daily_all_in_costs_websocket"
    ]


def test_daily_costs_register_after_customer_tariff_authority_and_before_load_plans() -> None:
    setup = _async_function(_tree(), "async_setup_entry")
    customer = _call_lines(setup, "async_register_customer_tariff_proposals_websocket")
    manual = _call_lines(setup, "async_register_manual_customer_tariff_websocket")
    daily = _call_lines(setup, "async_register_daily_all_in_costs_websocket")
    load_plan = _call_lines(setup, "async_register_load_plan_websocket")

    assert len(customer) == len(manual) == len(daily) == len(load_plan) == 1
    assert customer[0] < manual[0] < daily[0] < load_plan[0]
