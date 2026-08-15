import ast
from pathlib import Path


SOURCE = Path("custom_components/frakon_energy/sensor.py")


def _tree() -> ast.Module:
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"Missing class {name}")


def _function(node: ast.ClassDef, name: str):
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == name:
            return child
    raise AssertionError(f"Missing function {name}")


def test_sensor_imports_effective_billing_tariff_resolver_and_not_legacy_prices() -> None:
    tree = _tree()
    resolver_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "billing_tariff_selection"
    ]
    assert len(resolver_imports) == 1
    assert [alias.name for alias in resolver_imports[0].names] == [
        "billing_tariff_selection_for_day"
    ]

    config_import = next(
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "config_flow"
    )
    imported = {alias.name for alias in config_import.names}
    assert "CONF_PRICE_VT" not in imported
    assert "CONF_PRICE_NT" not in imported
    assert "CONF_FIXED_MONTHLY" not in imported

    source = SOURCE.read_text(encoding="utf-8")
    assert "TariffPrices(" not in source
    assert "options[CONF_PRICE_VT]" not in source
    assert "options[CONF_PRICE_NT]" not in source
    assert "options[CONF_FIXED_MONTHLY]" not in source


def test_billing_values_use_selected_all_in_prices_and_fail_closed() -> None:
    billing = _class(_tree(), "FrakonBillingSensor")
    values = _function(billing, "_values")
    calls = [
        child
        for child in ast.walk(values)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == "billing_tariff_selection_for_day"
    ]
    assert len(calls) == 1

    cost_calls = [
        child
        for child in ast.walk(values)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == "calculate_cost_projection"
    ]
    assert len(cost_calls) == 1
    prices = next(keyword.value for keyword in cost_calls[0].keywords if keyword.arg == "prices")
    assert isinstance(prices, ast.Attribute)
    assert isinstance(prices.value, ast.Name)
    assert prices.value.id == "tariff"
    assert prices.attr == "prices"

    handlers = [
        handler
        for child in ast.walk(values)
        if isinstance(child, ast.Try)
        for handler in child.handlers
    ]
    handled_names = set()
    for handler in handlers:
        if isinstance(handler.type, ast.Tuple):
            handled_names.update(
                item.id for item in handler.type.elts if isinstance(item, ast.Name)
            )
    assert "LookupError" in handled_names
    assert "ValueError" in handled_names


def test_billing_attributes_expose_effective_tariff_authority() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    for key in (
        '"price_source"',
        '"price_vt_czk_kwh"',
        '"price_nt_czk_kwh"',
        '"fixed_monthly_czk"',
        '"all_in_tariff_fingerprint"',
        '"tariff_supplier"',
        '"tariff_product_name"',
        '"tariff_distribution_tariff"',
        '"tariff_breaker_code"',
        '"tariff_valid_from"',
        '"tariff_valid_to"',
    ):
        assert key in source
    assert 'tariff.source.value if tariff is not None else "unavailable"' in source
