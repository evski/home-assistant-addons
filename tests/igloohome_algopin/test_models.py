"""Prefix check. IGB4 is on the docs table. A retail name is not a device id."""

from custom_components.igloohome_algopin.models import (
    classify_algopin,
    device_label,
    lookup_model,
)


def test_igb4_prefix_is_allowed_without_naming_a_product():
    metal = lookup_model("IGB403fe3b9d")
    assert metal is not None
    assert metal.prefix == "IGB4"
    assert metal.algopin is True
    assert classify_algopin("IGB403fe3b9d", "Lock") == "allow"
    label = device_label("Front door", "IGB403fe3b9d", "Lock")
    assert "IGB403fe3b9d" in label
    assert "prefix IGB4" in label
    assert "Deadbolt" not in label
    assert "Smart Deadbolt" not in label


def test_retail_name_is_not_a_device_and_is_not_compatible():
    assert lookup_model("Smart Deadbolt 4 Metal Grey") is None
    assert classify_algopin("Smart Deadbolt 4 Metal Grey", "") == "unconfirmed"
    label = device_label("Front door", "SmartDeadbolt4", "")
    assert "Deadbolt 2S" not in label
    assert "not a published prefix" in label


def test_retrofit_and_bridge_type_are_denied_from_real_fields():
    retrofit = lookup_model("OE1X01cd3a5f")
    assert retrofit is not None
    assert retrofit.algopin is False
    assert classify_algopin("OE1X01cd3a5f", "Lock") == "deny"
    assert classify_algopin("IGB403fe3b9d", "Bridge") == "deny"
    assert classify_algopin("IEF101abc123", "Fob") == "deny"


def test_unknown_prefix_stays_unconfirmed_and_has_no_model():
    assert lookup_model("ZZZZ01abcdef") is None
    assert classify_algopin("ZZZZ01abcdef", "Lock") == "unconfirmed"
    assert classify_algopin("ZZZZ01abcdef", "") == "unconfirmed"
    label = device_label("Front door", "ZZZZ01abcdef", "Lock")
    assert "not a published prefix" in label
    assert "igloohome account" in label


def test_longer_prefix_wins_for_the_table_check_only():
    assert lookup_model("IGB4A03fe3b9x").prefix == "IGB4A"
    assert lookup_model("IGB403fe3b9d").prefix == "IGB4"
    assert classify_algopin("IGB4A03fe3b9x", "Lock") == "allow"


def test_bridge_prefixes():
    assert lookup_model("EB1X01abc123").kind == "bridge"
    assert lookup_model("EB2X01abc123").name == "Bridge 2"
    assert classify_algopin("EB1X01abc123", "Bridge") == "deny"
    assert classify_algopin("EB1X01abc123", "") == "deny"
