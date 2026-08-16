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


def _function(node: ast.ClassDef, name: str) -> ast.FunctionDef:
    for child in node.body:
        if isinstance(child, ast.FunctionDef) and child.name == name:
            return child
    raise AssertionError(f"Missing function {name}")


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    matches = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name) and func.id == name:
            matches.append(child)
        elif isinstance(func, ast.Attribute) and func.attr == name:
            matches.append(child)
    return matches


def _subscripted_names(node: ast.AST) -> set[str]:
    result = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Subscript):
            continue
        key = child.slice
        if isinstance(key, ast.Name):
            result.add(key.id)
    return result


def test_billing_sensor_imports_fail_closed_tariff_selector() -> None:
    imports = [
        node
        for node in _tree().body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "billing_tariff_selection"
    ]
    assert len(imports) == 1
    assert {alias.name for alias in imports[0].names} == {
        "has_new_tariff_catalog",
        "select_billing_tariff_prices",
    }


def test_billing_values_use_selected_prices_and_never_read_legacy_price_options_directly() -> None:
    billing = _class(_tree(), "FrakonBillingSensor")
    values = _function(billing, "_values")

    assert len(_calls(values, "_tariff_selection")) == 1
    assert len(_calls(values, "TariffPrices")) == 0
    assert {
        "CONF_PRICE_VT",
        "CONF_PRICE_NT",
        "CONF_FIXED_MONTHLY",
    }.isdisjoint(_subscripted_names(values))

    calculate = _calls(values, "calculate_cost_projection")
    assert len(calculate) == 1
    prices_keyword = next(
        keyword
        for keyword in calculate[0].keywords
        if keyword.arg == "prices"
    )
    assert isinstance(prices_keyword.value, ast.Attribute)
    assert prices_keyword.value.attr == "prices"


def test_legacy_prices_are_isolated_to_legacy_helper() -> None:
    billing = _class(_tree(), "FrakonBillingSensor")
    legacy = _function(billing, "_legacy_tariff_prices")
    tariff_selection = _function(billing, "_tariff_selection")

    assert len(_calls(legacy, "TariffPrices")) == 1
    assert {
        "CONF_PRICE_VT",
        "CONF_PRICE_NT",
        "CONF_FIXED_MONTHLY",
    }.issubset(_subscripted_names(legacy))
    assert len(_calls(tariff_selection, "has_new_tariff_catalog")) == 1
    assert len(_calls(tariff_selection, "select_billing_tariff_prices")) == 1


def test_billing_attributes_publish_actual_tariff_authority_metadata() -> None:
    billing = _class(_tree(), "FrakonBillingSensor")
    attrs = _function(billing, "extra_state_attributes")
    source = ast.unparse(attrs)

    for field in (
        "price_source",
        "price_vt_czk_kwh",
        "price_nt_czk_kwh",
        "fixed_monthly_czk",
        "all_in_tariff_fingerprint",
        "tariff_authority_method",
        "tariff_supplier",
        "tariff_product_name",
    ):
        assert field in source
    assert "selection.prices.high_rate_czk_per_kwh" in source
    assert "selection.prices.low_rate_czk_per_kwh" in source
    assert "selection.prices.fixed_monthly_czk" in source


def test_pricing_failure_does_not_erase_independent_billing_facts() -> None:
    """A missing all-in tariff must not make saved advances and meter setup unknown."""

    billing = _class(_tree(), "FrakonBillingSensor")
    values = _function(billing, "_values")
    source = ast.unparse(values)

    base_marker = "'monthly_advance': advance.monthly_amount_czk"
    pricing_marker = "tariff_selection = self._tariff_selection(as_of)"
    assert base_marker in source
    assert pricing_marker in source
    assert source.index(base_marker) < source.index(pricing_marker)

    assert source.count("BillingCalculator.sum_advances") == 2
    assert "'baseline_vt': cycle.baseline.high_rate_kwh" in source
    assert "'baseline_nt': cycle.baseline.low_rate_kwh" in source
    assert "'cycle_start': cycle.start_date" in source
    assert "'settlement_date': cycle.expected_settlement_date" in source

    # Cost-dependent values start unavailable and only become populated after
    # tariff selection and cost projection complete successfully.
    for key in (
        "accrued_cost",
        "current_balance",
        "projected_cost",
        "projected_balance",
        "recommended_advance",
    ):
        assert f"'{key}': None" in source
    assert "return values" in source


def test_empty_daily_history_stays_unknown_instead_of_fake_zero() -> None:
    billing = _class(_tree(), "FrakonBillingSensor")
    values = _function(billing, "_values")
    source = ast.unparse(values)

    assert "if today_items:" in source
    assert "if month_items:" in source
    assert "'today_consumption': None" in source
    assert "'month_consumption': None" in source


def test_today_cost_uses_confirmed_daily_all_in_pricing_and_stays_unknown_without_authority() -> None:
    billing = _class(_tree(), "FrakonBillingSensor")
    values = _function(billing, "_values")
    source = ast.unparse(values)

    assert "'today_cost': None" in source
    assert len(_calls(values, "price_confirmed_daily_consumption")) == 1
    assert "item.variable_cost_czk" in source
    assert "priced_today" in source
