"""Tests for ``unity/embedded_mesh_extractor.py``.

The fixtures hand-build minimal Unity-style YAML containing a ``!u!43
Mesh`` document so the tests exercise the real parser path. The
SimpleFPS landmine prefab is also exercised end-to-end as a smoke
test of the channel-offset / stride logic on production data.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from unity.embedded_mesh_extractor import (
    EmbeddedMeshData,
    ExtractionFailure,
    FAILURE_EXTERNAL_STREAM_DATA,
    FAILURE_MESH_COMPRESSED,
    FAILURE_NOT_FOUND,
    FAILURE_NO_INDEX_DATA,
    FAILURE_NO_POSITION_CHANNEL,
    parse_embedded_mesh,
    reset_cache,
    serialize_obj,
    synthesize_fbx,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _f32(values: list[float]) -> bytes:
    return b"".join(struct.pack("<f", v) for v in values)


def _u16(values: list[int]) -> bytes:
    return b"".join(struct.pack("<H", v) for v in values)


def _hexify(data: bytes) -> str:
    return data.hex()


def _build_mesh_yaml(
    file_id: str,
    *,
    positions: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
    normals: list[tuple[float, float, float]] | None = None,
    uvs: list[tuple[float, float]] | None = None,
    extent: tuple[float, float, float] = (1.0, 1.0, 1.0),
    name: str = "TestMesh",
    extras: str = "",
) -> str:
    """Build a Unity-style .prefab YAML stub with a single !u!43 Mesh."""
    # Stream-0 layout: Position (offset 0, f32 x3 = 12) +
    # Normal (offset 12, f32 x3 = 12) + Tangent slot empty +
    # Color slot empty + UV0 (offset 24, f32 x2 = 8). Stride = 32 if UVs,
    # 24 if no UVs, 12 if positions-only.
    stride = 12
    vertex_count = len(positions)
    blob = bytearray()
    has_norm = normals is not None
    has_uv = uvs is not None
    if has_norm:
        stride = max(stride, 24)
    if has_uv:
        stride = max(stride, 32)
    # Build interleaved blob row by row.
    for i in range(vertex_count):
        row = bytearray(stride)
        row[0:12] = _f32(list(positions[i]))
        if has_norm:
            row[12:24] = _f32(list(normals[i]))
        if has_uv:
            row[24:32] = _f32(list(uvs[i]))
        blob += row

    # Pack the index buffer (always 16-bit triangles for the fixture).
    idx_blob = bytearray()
    for tri in triangles:
        idx_blob += _u16(list(tri))

    # ``m_Channels`` lives under ``m_VertexData`` (4-space indent). Its
    # list items therefore start at 4-space indent (the YAML "compact"
    # style where dashes align with the parent key).
    channels_yaml = (
        "    - { stream: 0, offset: 0, format: 0, dimension: 3 }\n"   # Position
        f"    - {{ stream: 0, offset: 12, format: 0, dimension: {3 if has_norm else 0} }}\n"  # Normal
        "    - { stream: 0, offset: 0, format: 0, dimension: 0 }\n"   # Tangent (unused)
        "    - { stream: 0, offset: 0, format: 0, dimension: 0 }\n"   # Color (unused)
        f"    - {{ stream: 0, offset: 24, format: 0, dimension: {2 if has_uv else 0} }}\n"  # UV0
    )

    body = (
        "%YAML 1.1\n"
        "%TAG !u! tag:unity3d.com,2011:\n"
        f"--- !u!43 &{file_id}\n"
        "Mesh:\n"
        f"  m_Name: {name}\n"
        f"  m_MeshCompression: 0\n"
        f"  m_SubMeshes:\n"
        f"  - serializedVersion: 2\n"
        f"    firstByte: 0\n"
        f"    indexCount: {len(triangles) * 3}\n"
        f"    topology: 0\n"
        f"    baseVertex: 0\n"
        f"    firstVertex: 0\n"
        f"    vertexCount: {vertex_count}\n"
        f"  m_IndexFormat: 0\n"
        f"  m_IndexBuffer: '{_hexify(bytes(idx_blob))}'\n"
        f"  m_VertexData:\n"
        f"    m_VertexCount: {vertex_count}\n"
        f"    m_Channels:\n"
        f"{channels_yaml}"
        f"    m_DataSize: {len(blob)}\n"
        f"    _typelessdata: '{_hexify(bytes(blob))}'\n"
        f"  m_LocalAABB:\n"
        f"    m_Center: {{ x: 0, y: 0, z: 0 }}\n"
        f"    m_Extent: {{ x: {extent[0]}, y: {extent[1]}, z: {extent[2]} }}\n"
        f"{extras}"
    )
    return body


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


class TestSuccessPaths:
    def test_decodes_positions_indices_aabb(self, tmp_path: Path) -> None:
        """A 4-vertex tetra round-trips: positions/indices/aabb."""
        positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                     (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
        triangles = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
        f = tmp_path / "tetra.prefab"
        f.write_text(_build_mesh_yaml("100", positions=positions,
                                      triangles=triangles, extent=(0.5, 0.5, 0.5)))

        result = parse_embedded_mesh(f, "100")
        assert isinstance(result, EmbeddedMeshData)
        # Handedness: negate X+Y, leave Z. Tetra positions reflect that.
        assert result.positions == [
            (-0.0, -0.0, 0.0), (-1.0, -0.0, 0.0),
            (-0.0, -1.0, 0.0), (-0.0, -0.0, 1.0),
        ]
        assert result.triangles == triangles
        assert result.aabb_extent == (0.5, 0.5, 0.5)

    def test_decodes_normals_and_uvs(self, tmp_path: Path) -> None:
        """Optional Normal + UV0 channels round-trip when present."""
        positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        normals = [(0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (0.0, 0.0, 1.0)]
        uvs = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        triangles = [(0, 1, 2)]
        f = tmp_path / "tri.prefab"
        f.write_text(_build_mesh_yaml(
            "200", positions=positions, triangles=triangles,
            normals=normals, uvs=uvs,
        ))

        result = parse_embedded_mesh(f, "200")
        assert isinstance(result, EmbeddedMeshData)
        # Normals get the X+Y handedness flip too (rotation, not reflection).
        assert result.normals == [(-0.0, -0.0, 1.0)] * 3
        assert result.uvs == uvs

    def test_basevertex_applied_to_indices(self, tmp_path: Path) -> None:
        """Submeshes with baseVertex > 0 shift their decoded indices."""
        positions = [(0.0, 0.0, 0.0)] * 6     # 6 verts, two triangles' worth
        # Index buffer holds indices 0..2; baseVertex=3 should remap them.
        triangles_in_buffer = [(0, 1, 2)]
        f = tmp_path / "basevertex.prefab"
        text = _build_mesh_yaml("300", positions=positions,
                                triangles=triangles_in_buffer)
        # Patch baseVertex into the rendered YAML.
        text = text.replace("baseVertex: 0", "baseVertex: 3")
        f.write_text(text)

        result = parse_embedded_mesh(f, "300")
        assert isinstance(result, EmbeddedMeshData)
        assert result.triangles == [(3, 4, 5)]


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


class TestStructuredFailures:
    def test_missing_file_id(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.prefab"
        f.write_text("%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!1 &1\nGameObject: {}\n")
        result = parse_embedded_mesh(f, "999")
        assert isinstance(result, ExtractionFailure)
        assert result.reason == FAILURE_NOT_FOUND

    def test_external_stream_data(self, tmp_path: Path) -> None:
        positions = [(0.0, 0.0, 0.0)] * 3
        triangles = [(0, 1, 2)]
        text = _build_mesh_yaml(
            "400", positions=positions, triangles=triangles,
            extras="  m_StreamData:\n    path: SomeExternal.resS\n",
        )
        f = tmp_path / "ext.prefab"
        f.write_text(text)
        result = parse_embedded_mesh(f, "400")
        assert isinstance(result, ExtractionFailure)
        assert result.reason == FAILURE_EXTERNAL_STREAM_DATA

    def test_mesh_compression_rejected(self, tmp_path: Path) -> None:
        positions = [(0.0, 0.0, 0.0)] * 3
        triangles = [(0, 1, 2)]
        text = _build_mesh_yaml("500", positions=positions, triangles=triangles)
        text = text.replace("m_MeshCompression: 0", "m_MeshCompression: 2")
        f = tmp_path / "compressed.prefab"
        f.write_text(text)
        result = parse_embedded_mesh(f, "500")
        assert isinstance(result, ExtractionFailure)
        assert result.reason == FAILURE_MESH_COMPRESSED

    def test_position_must_be_float32(self, tmp_path: Path) -> None:
        positions = [(0.0, 0.0, 0.0)] * 3
        triangles = [(0, 1, 2)]
        text = _build_mesh_yaml("600", positions=positions, triangles=triangles)
        # Flip Position channel to format 1 (Float16) -- not supported on Position.
        text = text.replace(
            "- { stream: 0, offset: 0, format: 0, dimension: 3 }\n",
            "- { stream: 0, offset: 0, format: 1, dimension: 3 }\n",
            1,
        )
        f = tmp_path / "pos_fp16.prefab"
        f.write_text(text)
        result = parse_embedded_mesh(f, "600")
        assert isinstance(result, ExtractionFailure)
        # Either POSITION_NOT_FLOAT32 (preferred) or STRIDE_MISMATCH would
        # also be a valid early reject. Accept both.
        assert "Position" in result.reason or "stride" in result.reason

    def test_no_position_channel(self, tmp_path: Path) -> None:
        text = _build_mesh_yaml("700", positions=[(0.0, 0.0, 0.0)] * 3,
                                triangles=[(0, 1, 2)])
        # Zero out Position's dimension so it's not a valid Position channel.
        text = text.replace(
            "- { stream: 0, offset: 0, format: 0, dimension: 3 }\n",
            "- { stream: 0, offset: 0, format: 0, dimension: 0 }\n",
            1,
        )
        f = tmp_path / "nopos.prefab"
        f.write_text(text)
        result = parse_embedded_mesh(f, "700")
        assert isinstance(result, ExtractionFailure)
        assert result.reason == FAILURE_NO_POSITION_CHANNEL

    def test_empty_index_buffer(self, tmp_path: Path) -> None:
        text = _build_mesh_yaml("800", positions=[(0.0, 0.0, 0.0)] * 3,
                                triangles=[])
        f = tmp_path / "noidx.prefab"
        f.write_text(text)
        result = parse_embedded_mesh(f, "800")
        assert isinstance(result, ExtractionFailure)
        assert result.reason == FAILURE_NO_INDEX_DATA


# ---------------------------------------------------------------------------
# OBJ serialiser
# ---------------------------------------------------------------------------


class TestSerializeObj:
    def test_basic_obj_shape(self) -> None:
        m = EmbeddedMeshData(
            name="T",
            positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            triangles=[(0, 1, 2)],
        )
        text = serialize_obj(m).decode("utf-8")
        assert "v 0.000000 0.000000 0.000000" in text
        assert "f 1 2 3" in text                            # no uv/normal -> bare verts
        assert "o T" in text

    def test_obj_face_syntax_with_uvs_and_normals(self) -> None:
        m = EmbeddedMeshData(
            name="T",
            positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            normals=[(0.0, 0.0, 1.0)] * 3,
            uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
            triangles=[(0, 1, 2)],
        )
        text = serialize_obj(m).decode("utf-8")
        # Canonical `v/vt/vn`, NOT the `v//vn/vt` shape codex flagged.
        assert "f 1/1/1 2/2/2 3/3/3" in text
        assert "vt 0.000000 0.000000" in text
        assert "vn 0.000000 0.000000 1.000000" in text

    def test_normals_only_emits_v_dslash_vn(self) -> None:
        m = EmbeddedMeshData(
            name="T",
            positions=[(0.0, 0.0, 0.0)] * 3,
            normals=[(0.0, 0.0, 1.0)] * 3,
            triangles=[(0, 1, 2)],
        )
        text = serialize_obj(m).decode("utf-8")
        assert "f 1//1 2//2 3//3" in text


# ---------------------------------------------------------------------------
# Integration with the real SimpleFPS landmine prefab
# ---------------------------------------------------------------------------


class TestSynthesizeFbx:
    """Roblox Open Cloud's Assets API rejects ``model/obj`` uploads, so
    the embedded-mesh feature has to emit binary FBX. ``synthesize_fbx``
    clones a template FBX and replaces its Geometry node's Vertices +
    PolygonVertexIndex; these tests assert the round-trip preserves
    counts and uses the FBX-required negative-index encoding for the
    last vertex of each polygon.
    """

    TEMPLATE_FBX = Path(
        "/Users/jiazou/workspace/unity2rbxlx/test_projects/SimpleFPS/"
        "Assets/Standard Assets/Environment/Water (Basic)/Models/"
        "WaterBasicPlane.fbx"
    )

    def _toy_mesh(self) -> EmbeddedMeshData:
        return EmbeddedMeshData(
            name="ToyTri",
            positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                       (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)],
            triangles=[(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)],
        )

    def test_round_trip_vertex_and_triangle_counts(self) -> None:
        if not self.TEMPLATE_FBX.exists():
            pytest.skip("Template FBX unavailable")
        from converter.fbx_binary import (
            _child,
            _find_geometry_nodes,
            read_fbx,
        )

        mesh = self._toy_mesh()
        fbx_bytes = synthesize_fbx(mesh, self.TEMPLATE_FBX)
        # Round-trip via a temp file.
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".fbx", delete=False) as f:
            f.write(fbx_bytes)
            tmp = Path(f.name)
        try:
            version, roots, footer = read_fbx(tmp)
            assert footer, "synthesized FBX must preserve template footer"
            geos = _find_geometry_nodes(roots)
            assert len(geos) == 1
            verts = _child(geos[0], b"Vertices").properties[0].value
            pvi = _child(geos[0], b"PolygonVertexIndex").properties[0].value
            # Vertices is flat [x,y,z, x,y,z, ...].
            assert len(verts) == 3 * len(mesh.positions)
            assert len(pvi) == 3 * len(mesh.triangles)
        finally:
            tmp.unlink()

    def test_polygon_vertex_index_uses_negative_last_index(self) -> None:
        """FBX encodes the LAST vertex of each polygon as ``~i`` to
        mark the polygon boundary -- a quirk we have to honour even
        for triangulated meshes.
        """
        if not self.TEMPLATE_FBX.exists():
            pytest.skip("Template FBX unavailable")
        from converter.fbx_binary import (
            _child,
            _find_geometry_nodes,
            read_fbx,
        )
        mesh = EmbeddedMeshData(
            name="Sentinel",
            positions=[(0.0, 0.0, 0.0)] * 4,
            triangles=[(0, 1, 2)],  # last index should encode as ~2 == -3
        )
        fbx_bytes = synthesize_fbx(mesh, self.TEMPLATE_FBX)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".fbx", delete=False) as f:
            f.write(fbx_bytes)
            tmp = Path(f.name)
        try:
            _ver, roots, _ = read_fbx(tmp)
            pvi = _child(_find_geometry_nodes(roots)[0],
                         b"PolygonVertexIndex").properties[0].value
            assert pvi[:3] == [0, 1, -3], pvi[:6]
        finally:
            tmp.unlink()


class TestSimpleFpsLandmineRealAsset:
    """Production smoke test against the embedded mesh that motivated
    this whole feature. The real ``AT_Mine_LOD3.prefab`` ships a
    legacy NativeFormatImporter mesh with a non-trivial channel
    layout (Position+Normal+Tangent+Color+UV0) -- exactly the
    case the plan needed to support.
    """

    PREFAB = Path(
        "/Users/jiazou/workspace/unity2rbxlx/test_projects/SimpleFPS/"
        "Assets/AssetPack/AT Mine/at_mine_LOD3.prefab"
    )

    def test_real_landmine_decodes(self) -> None:
        if not self.PREFAB.exists():
            pytest.skip("SimpleFPS sample asset unavailable")
        # Find the embedded mesh's file_id by scanning headers (the
        # value is stable for this asset but recomputing it makes the
        # test robust to manual edits).
        import re
        text = self.PREFAB.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^--- !u!43 &(-?\d+)", text, re.MULTILINE)
        assert m, "Sample asset lost its embedded mesh!"
        file_id = m.group(1)
        result = parse_embedded_mesh(self.PREFAB, file_id)
        assert isinstance(result, EmbeddedMeshData), (
            f"failed to decode SimpleFPS mine: {result}"
        )
        # Real mine has hundreds of verts and triangles -- assert sanity.
        assert len(result.positions) > 100
        assert len(result.triangles) > 50
        # AABB extent reads ~ (0.1615, 0.0488, 0.1749) per the prefab.
        ex, ey, ez = result.aabb_extent
        assert 0.05 < ex < 0.5
        assert 0.01 < ey < 0.2
        assert 0.05 < ez < 0.5
        # OBJ serialises without error.
        obj_bytes = serialize_obj(result)
        assert b"f " in obj_bytes
        assert obj_bytes.count(b"\nv ") == len(result.positions)
