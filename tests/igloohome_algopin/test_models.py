"""Official prefix table. IGB4 is Deadbolt 2S Metal Grey. OE1X Retrofit is not AlgoPIN."""

from custom_components.igloohome_algopin.models import device_label, lookup_model, model_name


def test_igb4_metal_grey_supports_algopin_and_retrofit_does_not():
    metal = lookup_model("IGB403fe3b9d")
    assert metal is not None
    assert metal.name == "Deadbolt 2S Metal Grey"
    assert metal.algopin is True
    assert metal.kind == "lock"

    retrofit = lookup_model("OE1X01cd3a5f")
    assert retrofit is not None
    assert retrofit.name == "Retrofit Lock"
    assert retrofit.algopin is False


def test_longer_prefix_wins():
    assert model_name("IGB4A03fe3b9x") == "Deadbolt 2E"
    assert model_name("IGB403fe3b9d") == "Deadbolt 2S Metal Grey"


def test_unknown_prefix_has_no_invented_model():
    assert lookup_model("ZZZZ01abcdef") is None
    assert model_name("") is None
    assert device_label("Front door", "ZZZZ01abcdef") == "Front door — igloohome"


def test_label_includes_brand_and_official_model_after_the_id_is_known():
    assert device_label("Front door", "IGB403fe3b9d") == "Front door — igloohome Deadbolt 2S Metal Grey"


def test_bridge_prefixes():
    assert lookup_model("EB1X01abc123").kind == "bridge"
    assert lookup_model("EB2X01abc123").name == "Bridge 2"
    assert lookup_model("EB1X01abc123").algopin is False
