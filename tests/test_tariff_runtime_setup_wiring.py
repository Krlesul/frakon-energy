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


def test_tariff_runtime_lifecycle_helpers_are_imported() -> None:
    tree = _tree()
    imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "tariff_update_runtime"
    ]
    assert len(imports) == 1
    imported = {alias.name for alias in imports[0].names}
    assert imported == {
        "async_start_tariff_update_runtime",
        "async_stop_tariff_update_runtime",
    }


def test_tariff_runtime_starts_after_listener_and_execution_runtime() -> None:
    setup = _async_function(_tree(), "async_setup_entry")
    execution_lines = _call_lines(setup, "async_start_execution_runtimes")
    listener_lines = _call_lines(setup, "async_on_unload")
    tariff_lines = _call_lines(setup, "async_start_tariff_update_runtime")

    assert len(execution_lines) == 1
    assert len(listener_lines) == 1
    assert len(tariff_lines) == 1
    assert execution_lines[0] < listener_lines[0] < tariff_lines[0]

    returns = sorted(
        node.lineno
        for node in ast.walk(setup)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
        and node.value.value is True
    )
    assert returns == [tariff_lines[0] + 1]


def test_unload_cleanup_stops_tariff_runtime_before_execution_workers() -> None:
    cleanup = _async_function(_tree(), "_async_cleanup_unloaded_entry")
    tariff_lines = _call_lines(cleanup, "async_stop_tariff_update_runtime")
    execution_lines = _call_lines(cleanup, "async_stop_execution_runtimes")

    assert len(tariff_lines) == 1
    assert len(execution_lines) == 1
    assert tariff_lines[0] < execution_lines[0]
