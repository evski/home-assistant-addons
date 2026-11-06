"""Repository shape. This add-on repo must not pretend to be a HACS integration."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "custom_components" / "igloohome_algopin"


def test_manifest_pins_the_verified_package_and_matches_the_version_constant():
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["domain"] == "igloohome_algopin"
    assert manifest["requirements"] == ["igloohome-api==0.1.2"]
    assert manifest["version"] == "0.1.0"
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "service"
    source = (COMPONENT / "const.py").read_text()
    assert 'VERSION = "0.1.0"' in source
    assert "igloohome-api==0.1.2" in source


def test_addon_repository_is_not_a_hacs_integration_repo():
    assert not (ROOT / "hacs.json").exists()
    repository = json.loads((ROOT / "repository.json").read_text())
    assert "igloohome" not in json.dumps(repository).lower()
    example = json.loads((ROOT / "igloohome-algopin-install" / "hacs.json").read_text())
    assert example["domain" if "domain" in example else "domains"]
    assert example["content_in_root"] is False
    readme = (ROOT / "igloohome-algopin-install" / "README.md").read_text()
    assert "/config/custom_components" in readme
    assert "not a HACS" in readme or "Not a HACS" in readme


def test_component_does_not_persist_pins_or_fire_events():
    text = "\n".join(path.read_text() for path in COMPONENT.glob("*.py"))
    assert "helpers.storage" not in text
    assert "async_fire(" not in text
    assert "SupportsResponse.ONLY" in text
    assert "async_add_executor_job" not in text


def test_strings_json_is_valid():
    strings = json.loads((COMPONENT / "strings.json").read_text())
    assert "create_hourly_algopin" in strings["services"]
    assert "create_daily_algopin" in strings["services"]
    assert "revoke_algopin" in strings["services"]
    english = COMPONENT / "translations" / "en.json"
    assert english.is_file()
    assert json.loads(english.read_text()) == strings
