"""
Canonical Roblox material name -> enum token table.

Roblox stores ``BasePart.Material`` as an int32 enum token. This module is the
single source of truth for the material name<->token mapping, shared by:

* ``rbxlx_writer`` — emits the integer token into the ``.rbxlx`` XML
* ``luau_place_builder`` — emits ``Enum.Material.<name>`` into headless Luau

Keeping one table avoids the two from drifting apart.
"""

from __future__ import annotations

# Material name -> Roblox Enum.Material integer token.
MATERIAL_NAME_TO_TOKEN: dict[str, int] = {
    "Plastic": 256, "SmoothPlastic": 272, "Neon": 288,
    "Wood": 512, "WoodPlanks": 528, "Marble": 784, "Basalt": 788,
    "Slate": 800, "CrackedLava": 804, "Concrete": 816,
    "Limestone": 820, "Pavement": 836, "Granite": 832,
    "Brick": 848, "Pebble": 864, "Cobblestone": 880,
    "Rock": 896, "Sandstone": 912, "CorrodedMetal": 1040,
    "DiamondPlate": 1056, "Foil": 1072, "Metal": 1088,
    "Grass": 1280, "LeafyGrass": 1284, "Sand": 1296,
    "Fabric": 1312, "Snow": 1328, "Mud": 1344,
    "Ground": 1360, "Asphalt": 1376, "Salt": 1392,
    "Ice": 1536, "Glacier": 1552, "Glass": 1568,
    "ForceField": 1584, "Air": 1792, "Water": 2048,
}

# Token used when a material name is unknown — Enum.Material.Plastic.
DEFAULT_MATERIAL_TOKEN = 256


def material_token(name: str) -> int:
    """Return the enum token for a material name, or the Plastic default."""
    return MATERIAL_NAME_TO_TOKEN.get(name, DEFAULT_MATERIAL_TOKEN)
