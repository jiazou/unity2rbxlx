"""Tests for converter.terrain_converter.generate_terrain_luau.

The pure heightmap -> Luau code path had no coverage. These tests pin the
sparse-encoding format, the splat-map vs. height-based material branches, the
Unity layer-name -> Roblox material mapping, and the Unity->Roblox Z negation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from converter.terrain_converter import generate_terrain_luau

STUDS = config.STUDS_PER_METER


def _terrain(heights, resolution, *, width=2.0, length=2.0, max_height=10.0, **extra):
    """Build a terrain_data dict shaped like read_unity_terrain()'s output."""
    data = {
        "heights": heights,
        "resolution": resolution,
        "scale": (1.0, max_height, 1.0),
        "terrain_size": (width, max_height, length),
        "layers": [],
    }
    data.update(extra)
    return data


class TestSparseEncoding:
    def test_flat_zero_terrain_emits_no_columns(self):
        # All heights 0 -> every column is below the 0.5-stud threshold -> empty data.
        luau = generate_terrain_luau(_terrain([0.0] * 4, 2))
        assert 'local data = ""' in luau
        assert "-- Sparse entries: 0 non-zero columns" in luau

    def test_full_height_terrain_emits_columns(self):
        luau = generate_terrain_luau(_terrain([1.0] * 4, 2, max_height=10.0))
        # 1.0 normalized * 10m * STUDS, rounded to 0.1.
        expected_h = round(1.0 * 10.0 * STUDS, 1)
        assert 'local data = ""' not in luau
        assert f",{expected_h}" in luau
        assert f"local maxH = {1.0 * 10.0 * STUDS:.1f}" in luau

    def test_threshold_only_keeps_columns_above_half_stud(self):
        # Height that maps to < 0.5 studs must be dropped, not encoded.
        tiny = 0.4 / (10.0 * STUDS)  # normalized value producing ~0.4 studs
        luau = generate_terrain_luau(_terrain([tiny] * 4, 2, max_height=10.0))
        assert 'local data = ""' in luau


class TestMaterialBranches:
    def test_height_based_branch_when_no_splat(self):
        luau = generate_terrain_luau(_terrain([1.0] * 4, 2))
        assert "local function gM(h)" in luau
        assert "gM(h, mc)" not in luau
        assert "local mats = {" not in luau
        assert "-- Material source: height-based" in luau

    def test_splat_branch_emits_material_table(self):
        luau = generate_terrain_luau(
            _terrain(
                [1.0] * 4,
                2,
                layers=["Rock"],
                splat_alphas=[[1.0, 1.0, 1.0, 1.0]],
                splat_resolution=2,
            )
        )
        assert "local function gM(h, mc)" in luau
        assert "local mats = {" in luau
        assert "Enum.Material.Rock" in luau
        assert "-- Material source: splat map" in luau

    def test_dominant_layer_wins_per_column(self):
        # Two layers; layer 1 (Snow) dominates everywhere -> material code "N".
        luau = generate_terrain_luau(
            _terrain(
                [1.0] * 4,
                2,
                layers=["Grass", "Snow"],
                splat_alphas=[[0.1, 0.1, 0.1, 0.1], [0.9, 0.9, 0.9, 0.9]],
                splat_resolution=2,
            )
        )
        # Sparse entries carry a single-char material code as the 4th field.
        assert ",N" in luau
        assert ",G" not in luau.split('local data = "')[1].split('"')[0]


class TestLayerNameMapping:
    def test_unity_layer_keyword_maps_to_roblox_material(self):
        # "MountainCliff" contains "mountain" -> Rock -> code "R".
        luau = generate_terrain_luau(
            _terrain(
                [1.0] * 4,
                2,
                layers=["MountainCliff"],
                splat_alphas=[[1.0, 1.0, 1.0, 1.0]],
                splat_resolution=2,
            )
        )
        data_str = luau.split('local data = "')[1].split('"')[0]
        assert ",R" in data_str

    def test_unknown_layer_defaults_to_grass(self):
        luau = generate_terrain_luau(
            _terrain(
                [1.0] * 4,
                2,
                layers=["Quux"],
                splat_alphas=[[1.0, 1.0, 1.0, 1.0]],
                splat_resolution=2,
            )
        )
        data_str = luau.split('local data = "')[1].split('"')[0]
        assert ",G" in data_str


class TestCoordinatesAndVoxel:
    def test_terrain_position_threaded_into_origin(self):
        luau = generate_terrain_luau(
            _terrain([1.0] * 4, 2), terrain_position=(10.0, 5.0, 20.0)
        )
        assert "local oX = 10.0" in luau
        assert "local oY = 5.0" in luau
        assert "local oZ = 20.0" in luau

    def test_z_axis_is_negated_for_roblox(self):
        # Unity +Z maps to Roblox -Z; the runtime loop must subtract.
        luau = generate_terrain_luau(_terrain([1.0] * 4, 2))
        assert "oZ - z * VOXEL" in luau
        assert "oX + x * VOXEL" in luau

    def test_voxel_size_param_is_used(self):
        luau = generate_terrain_luau(_terrain([1.0] * 4, 2), voxel_size=8)
        assert "local VOXEL = 8" in luau
