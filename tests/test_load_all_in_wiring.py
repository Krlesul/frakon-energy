import ast
from pathlib import Path


SOURCE = Path("custom_components/frakon_energy/load_plan_ws_api.py")


def _tree() -> ast.Module:
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def _async_function(name: str) -> ast.AsyncFunctionDef:
    for node in _tree().body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Missing async function {name}")


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    result = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name) and func.id == name:
            result.append(child)
        elif isinstance(func, ast.Attribute) and func.attr == name:
            result.append(child)
    return result


def test_profile_preview_imports_all_in_estimate_boundary() -> None:
    imports = [
        node
        for node in _tree().body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "load_all_in_estimate"
    ]
    assert len(imports) == 1
    assert {alias.name for alias in imports[0].names} == {
        "build_confirmed_all_in_load_estimate",
        "unavailable_all_in_load_estimate",
    }


def test_generic_spot_preview_remains_optimizer_only() -> None:
    generic = _async_function("async_preview_load_plan")
    assert len(_calls(generic, "plan_flexible_load")) == 1
    assert len(_calls(generic, "build_confirmed_all_in_load_estimate")) == 0
    assert len(_calls(generic, "unavailable_all_in_load_estimate")) == 0


def test_persisted_profile_preview_adds_separate_all_in_estimate() -> None:
    profile = _async_function("async_preview_profile_plan")
    build = _calls(profile, "build_confirmed_all_in_load_estimate")
    unavailable = _calls(profile, "unavailable_all_in_load_estimate")

    assert len(build) == 1
    assert len(unavailable) == 1
    assert ast.unparse(build[0].args[0]) == "entry.options"
    kwargs = {item.arg: ast.unparse(item.value) for item in build[0].keywords}
    assert kwargs == {
        "starts_at": "plan['starts_at']",
        "ends_at": "plan['ends_at']",
        "power_kw": "plan['power_kw']",
    }
    source = ast.unparse(profile)
    assert "plan['all_in_estimate']" in source
    assert "except (KeyError, LookupError, TypeError, ValueError)" in source


def test_profile_enrichment_does_not_overwrite_spot_cost_fields() -> None:
    profile = _async_function("async_preview_profile_plan")
    for child in ast.walk(profile):
        if not isinstance(child, ast.Subscript):
            continue
        if not isinstance(child.slice, ast.Constant):
            continue
        assert child.slice.value not in {
            "estimated_cost_czk",
            "average_czk_kwh",
            "minimum_czk_kwh",
            "maximum_czk_kwh",
        }
