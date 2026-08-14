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


def test_tariff_discovery_websocket_is_imported_once() -> None:
    imports = [
        node
        for node in _tree().body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "tariff_discovery_ws_api"
    ]
    assert len(imports) == 1
    assert [alias.name for alias in imports[0].names] == [
        "async_register_tariff_discovery_websocket"
    ]


def test_tariff_discovery_websocket_is_registered_once_before_platform_forwarding() -> None:
    setup = _setup(_tree())
    registration = _call_lines(setup, "async_register_tariff_discovery_websocket")
    forwarding = _call_lines(setup, "async_forward_entry_setups")

    assert len(registration) == 1
    assert len(forwarding) == 1
    assert registration[0] < forwarding[0]
