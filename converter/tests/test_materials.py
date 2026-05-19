"""Tests for roblox.materials — the canonical material name->token table."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from roblox.materials import (
    MATERIAL_NAME_TO_TOKEN,
    DEFAULT_MATERIAL_TOKEN,
    material_token,
)


class TestMaterialTable:
    def test_default_is_plastic(self):
        assert DEFAULT_MATERIAL_TOKEN == 256
        assert MATERIAL_NAME_TO_TOKEN["Plastic"] == 256

    def test_known_tokens(self):
        # Spot-check a spread of tokens — these are load-bearing for the
        # rbxlx binary/XML output and must not change.
        assert MATERIAL_NAME_TO_TOKEN["SmoothPlastic"] == 272
        assert MATERIAL_NAME_TO_TOKEN["Neon"] == 288
        assert MATERIAL_NAME_TO_TOKEN["Wood"] == 512
        assert MATERIAL_NAME_TO_TOKEN["Metal"] == 1088
        assert MATERIAL_NAME_TO_TOKEN["Glass"] == 1568
        assert MATERIAL_NAME_TO_TOKEN["ForceField"] == 1584
        assert MATERIAL_NAME_TO_TOKEN["Water"] == 2048

    def test_table_size(self):
        assert len(MATERIAL_NAME_TO_TOKEN) == 37

    def test_all_tokens_unique(self):
        tokens = list(MATERIAL_NAME_TO_TOKEN.values())
        assert len(tokens) == len(set(tokens))


class TestMaterialToken:
    def test_known_name(self):
        assert material_token("Concrete") == MATERIAL_NAME_TO_TOKEN["Concrete"]

    def test_unknown_name_falls_back_to_plastic(self):
        assert material_token("NotARealMaterial") == DEFAULT_MATERIAL_TOKEN
        assert material_token("") == DEFAULT_MATERIAL_TOKEN
