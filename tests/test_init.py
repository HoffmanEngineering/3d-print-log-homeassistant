"""Scaffold tests."""

import json
from pathlib import Path

from awesomeversion import AwesomeVersion

from custom_components.printlog.const import DOMAIN

MANIFEST = Path(__file__).parent.parent / "custom_components" / "printlog" / "manifest.json"


def test_manifest_matches_const():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["domain"] == DOMAIN
    assert AwesomeVersion(manifest["version"]).valid
