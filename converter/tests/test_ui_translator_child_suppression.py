"""PR3c carve-out 2 — generic-only serialized-field child suppression in ui_translator.

Pinned by ``converter/docs/design/scene-runtime-contract.md`` Piece 4
("asset/prefab serialized-field child suppression"). Under generic the
host runtime owns instantiation of prefab content + asset wiring on UI
controllers via ``host.instantiatePrefab``; emitting the static child
tree as well would double-stamp the tree (runtime adds its copy; static
copy never goes away).

Coverage:
- Under ``--scene-runtime=generic``, a Canvas containing a UI
  GameObject that hosts a runtime-bearing controller with a serialized
  field pointing at a prefab gets no static descendants under that
  element. The element's ``_SceneRuntimeId`` is the binding the runtime
  uses to populate it.
- The same input under ``--scene-runtime=legacy`` keeps the static
  descendants intact (byte-unchanged emit).
- A non-runtime-bearing controller with the same shape does NOT
  trigger suppression — the carve-out is keyed on the planner's
  ``runtime_bearing`` verdict.
- The helper ``_collect_ui_child_suppression_ids`` returns the empty
  set on missing / partial planner artifacts so legacy never sees
  spurious suppression.
- Snapshot: the legacy emit is identical regardless of whether a
  populated planner artifact is threaded through.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from converter.scene_converter import (
    _collect_ui_child_suppression_ids,
    convert_scene,
)
from converter.ui_translator import convert_canvas
from core.unity_types import ComponentData, ParsedScene, SceneNode


SCENE_PATH = Path("Assets/Scenes/UI.unity")


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------

def _make_node(
    name: str, file_id: str, *, active: bool = True,
    children: list[SceneNode] | None = None,
    components: list[ComponentData] | None = None,
) -> SceneNode:
    return SceneNode(
        name=name,
        file_id=file_id,
        active=active,
        layer=0,
        tag="Untagged",
        components=components or [],
        children=children or [],
        parent_file_id=None,
        position=(0.0, 0.0, 0.0),
        rotation=(0.0, 0.0, 0.0, 1.0),
        scale=(1.0, 1.0, 1.0),
    )


def _canvas_with_controller_and_static_descendants() -> SceneNode:
    """Canvas → ControllerHost (Frame w/ MonoBehaviour) → StaticChild,
    StaticGrandchild. The runtime-bearing controller lives on
    ControllerHost (file_id=200); its serialized field points at a
    prefab GUID (recorded out-of-band on the synthetic scene_runtime
    artifact passed to ``convert_canvas``).
    """
    grandchild = _make_node("StaticGrandchild", file_id="202")
    static_child = _make_node(
        "StaticChild", file_id="201",
        children=[grandchild],
    )
    controller_host = _make_node(
        "ControllerHost", file_id="200",
        components=[
            ComponentData(
                component_type="RectTransform", file_id="2001",
                properties={},
            ),
            ComponentData(
                component_type="MonoBehaviour", file_id="2002",
                properties={
                    # Planner classified this MB as runtime-bearing; the
                    # serialized field with the prefab ref is the signal
                    # that triggers child suppression.
                    "m_inventoryItemPrefab": {"fileID": 0, "guid": "abc123guid"},
                },
            ),
        ],
        children=[static_child],
    )
    return _make_node(
        "Canvas", file_id="100",
        components=[
            ComponentData(component_type="Canvas", file_id="1001", properties={}),
        ],
        children=[controller_host],
    )


def _find_element(elements, name):
    for e in elements:
        if e.name == name:
            return e
        found = _find_element(e.children, name)
        if found is not None:
            return found
    return None


def _all_element_names(elements) -> list[str]:
    out: list[str] = []
    for e in elements:
        out.append(e.name)
        out.extend(_all_element_names(e.children))
    return out


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

class TestCollectUiChildSuppressionIds:
    """Direct coverage on the planner-artifact walker so a schema
    drift surfaces here instead of bubbling up through ``convert_scene``."""

    def test_empty_input_returns_empty_set(self):
        assert _collect_ui_child_suppression_ids(None) == frozenset()
        assert _collect_ui_child_suppression_ids({}) == frozenset()

    def test_runtime_bearing_mb_with_prefab_ref_marks_host_go(self):
        artifact = {
            "modules": {
                "InventoryController": {
                    "stem": "InventoryController",
                    "class_name": "InventoryController",
                    "runtime_bearing": True,
                },
            },
            "scenes": {
                str(SCENE_PATH): {
                    "instances": [
                        {
                            "instance_id": f"{SCENE_PATH.as_posix()}:2002",
                            "script_id": "InventoryController",
                            "game_object_id": f"{SCENE_PATH.as_posix()}:200",
                            "active": True,
                            "enabled": True,
                            "config": {},
                        },
                    ],
                    "references": [
                        {
                            "from": f"{SCENE_PATH.as_posix()}:2002",
                            "field": "itemPrefab",
                            "index": None,
                            "target_kind": "prefab",
                            "target_ref": "guidpath:Assets/Prefabs/Item.prefab",
                            "target_is_ui": False,
                        },
                    ],
                    "lifecycle_order": [],
                },
            },
        }
        assert _collect_ui_child_suppression_ids(artifact) == frozenset({
            f"{SCENE_PATH.as_posix()}:200",
        })

    def test_non_runtime_bearing_mb_does_not_trigger(self):
        """The carve-out gates on ``runtime_bearing`` — a legacy-routed
        controller with the same prefab ref must NOT trigger suppression
        (the host runtime never wires it; static emit is the only source)."""
        artifact = {
            "modules": {
                "OldController": {
                    "stem": "OldController",
                    "class_name": "OldController",
                    "runtime_bearing": False,
                },
            },
            "scenes": {
                str(SCENE_PATH): {
                    "instances": [
                        {
                            "instance_id": f"{SCENE_PATH.as_posix()}:2002",
                            "script_id": "OldController",
                            "game_object_id": f"{SCENE_PATH.as_posix()}:200",
                            "active": True,
                            "enabled": True,
                            "config": {},
                        },
                    ],
                    "references": [
                        {
                            "from": f"{SCENE_PATH.as_posix()}:2002",
                            "field": "itemPrefab",
                            "index": None,
                            "target_kind": "prefab",
                            "target_ref": "guidpath:Assets/Prefabs/Item.prefab",
                            "target_is_ui": False,
                        },
                    ],
                    "lifecycle_order": [],
                },
            },
        }
        assert _collect_ui_child_suppression_ids(artifact) == frozenset()

    def test_asset_ref_also_triggers_when_runtime_bearing(self):
        artifact = {
            "modules": {
                "AssetController": {
                    "stem": "AssetController",
                    "class_name": "AssetController",
                    "runtime_bearing": True,
                },
            },
            "scenes": {
                str(SCENE_PATH): {
                    "instances": [
                        {
                            "instance_id": f"{SCENE_PATH.as_posix()}:5",
                            "script_id": "AssetController",
                            "game_object_id": f"{SCENE_PATH.as_posix()}:4",
                            "active": True,
                            "enabled": True,
                            "config": {},
                        },
                    ],
                    "references": [
                        {
                            "from": f"{SCENE_PATH.as_posix()}:5",
                            "field": "spriteAsset",
                            "index": None,
                            "target_kind": "asset",
                            "target_ref": "abc123guid",
                            "target_is_ui": False,
                        },
                    ],
                    "lifecycle_order": [],
                },
            },
        }
        assert _collect_ui_child_suppression_ids(artifact) == frozenset({
            f"{SCENE_PATH.as_posix()}:4",
        })

    def test_gameobject_ref_does_not_trigger(self):
        """Carve-out is ONLY for cross-asset / prefab refs. Local
        gameobject/component refs are wired by the host without
        instantiation, so the static emit must stay."""
        artifact = {
            "modules": {
                "Ctrl": {
                    "stem": "Ctrl", "class_name": "Ctrl",
                    "runtime_bearing": True,
                },
            },
            "scenes": {
                str(SCENE_PATH): {
                    "instances": [
                        {
                            "instance_id": f"{SCENE_PATH.as_posix()}:5",
                            "script_id": "Ctrl",
                            "game_object_id": f"{SCENE_PATH.as_posix()}:4",
                            "active": True,
                            "enabled": True,
                            "config": {},
                        },
                    ],
                    "references": [
                        {
                            "from": f"{SCENE_PATH.as_posix()}:5",
                            "field": "targetGo",
                            "index": None,
                            "target_kind": "gameobject",
                            "target_ref": f"{SCENE_PATH.as_posix()}:10",
                            "target_is_ui": False,
                        },
                    ],
                    "lifecycle_order": [],
                },
            },
        }
        assert _collect_ui_child_suppression_ids(artifact) == frozenset()


# ---------------------------------------------------------------------------
# convert_canvas-level integration — mode gating
# ---------------------------------------------------------------------------

class TestConvertCanvasChildSuppression:
    """Direct convert_canvas tests (no full ``convert_scene`` needed)
    so the carve-out's effect is observable without scene-runtime
    plumbing every test through ``parse_scene``."""

    def test_generic_drops_static_descendants_under_controller_host(self):
        canvas = _canvas_with_controller_and_static_descendants()
        suppress = frozenset({"Assets/Scenes/UI.unity:200"})

        guis = convert_canvas(
            [canvas],
            scene_namespace="Assets/Scenes/UI.unity",
            scene_runtime_mode="generic",
            suppress_static_children_ids=suppress,
        )

        assert len(guis) == 1
        controller_host = _find_element(guis[0].elements, "ControllerHost")
        assert controller_host is not None
        assert controller_host.children == [], (
            "controller host under generic must have no static descendants"
        )
        # The host itself is still stamped — the runtime resolves the
        # prefab population via the _SceneRuntimeId binding.
        assert (
            controller_host.attributes["_SceneRuntimeId"]
            == "Assets/Scenes/UI.unity:200"
        )
        # The descendants the static emit would have produced are gone
        # from the entire tree.
        assert "StaticChild" not in _all_element_names(guis[0].elements)
        assert "StaticGrandchild" not in _all_element_names(guis[0].elements)

    def test_legacy_keeps_static_descendants(self):
        canvas = _canvas_with_controller_and_static_descendants()

        # No mode / no suppression ids → legacy emit path.
        guis = convert_canvas(
            [canvas], scene_namespace="Assets/Scenes/UI.unity",
        )

        controller_host = _find_element(guis[0].elements, "ControllerHost")
        assert controller_host is not None
        assert _find_element(controller_host.children, "StaticChild") is not None
        assert _find_element(
            controller_host.children, "StaticGrandchild"
        ) is not None

    def test_legacy_mode_with_populated_suppression_still_emits(self):
        """Belt-and-suspenders: even if the caller (incorrectly) passes a
        populated ``suppress_static_children_ids`` under
        ``scene_runtime_mode="legacy"``, the legacy emit MUST stay
        unchanged — the gate is the mode AND the set, with mode being
        the authoritative gate."""
        canvas = _canvas_with_controller_and_static_descendants()
        suppress = frozenset({"Assets/Scenes/UI.unity:200"})

        guis = convert_canvas(
            [canvas],
            scene_namespace="Assets/Scenes/UI.unity",
            scene_runtime_mode="legacy",
            suppress_static_children_ids=suppress,
        )

        controller_host = _find_element(guis[0].elements, "ControllerHost")
        assert controller_host is not None
        assert _find_element(controller_host.children, "StaticChild") is not None

    def test_generic_no_suppression_set_keeps_static_descendants(self):
        """When the planner artifact yields no GO that needs
        suppression (e.g. no runtime-bearing UI controllers), generic
        emit is identical to legacy. Pins that the carve-out fires only
        on confirmed signals — no silent over-suppression."""
        canvas = _canvas_with_controller_and_static_descendants()

        guis = convert_canvas(
            [canvas],
            scene_namespace="Assets/Scenes/UI.unity",
            scene_runtime_mode="generic",
            suppress_static_children_ids=frozenset(),
        )

        controller_host = _find_element(guis[0].elements, "ControllerHost")
        assert controller_host is not None
        assert _find_element(controller_host.children, "StaticChild") is not None


# ---------------------------------------------------------------------------
# convert_scene-level end-to-end snapshot
# ---------------------------------------------------------------------------

def _build_scene(tmp_path: Path, canvas_root: SceneNode) -> ParsedScene:
    scene_file = tmp_path / SCENE_PATH
    scene_file.parent.mkdir(parents=True, exist_ok=True)
    scene_file.touch(exist_ok=True)

    def _index(node: SceneNode, parent_fid: str | None) -> dict[str, SceneNode]:
        node.parent_file_id = parent_fid
        idx = {node.file_id: node}
        for c in node.children:
            idx.update(_index(c, node.file_id))
        return idx

    return ParsedScene(
        scene_path=scene_file, roots=[canvas_root],
        all_nodes=_index(canvas_root, None),
    )


def _full_suppression_artifact() -> dict[str, object]:
    """A planner artifact that marks ControllerHost (file_id=200) as
    runtime-bearing + asset/prefab-bearing. Matches the canvas built by
    ``_canvas_with_controller_and_static_descendants``."""
    return {
        "modules": {
            "InventoryController": {
                "stem": "InventoryController",
                "class_name": "InventoryController",
                "runtime_bearing": True,
            },
        },
        "scenes": {
            str(SCENE_PATH): {
                "instances": [
                    {
                        "instance_id": f"{SCENE_PATH.as_posix()}:2002",
                        "script_id": "InventoryController",
                        "game_object_id": f"{SCENE_PATH.as_posix()}:200",
                        "active": True,
                        "enabled": True,
                        "config": {},
                    },
                ],
                "references": [
                    {
                        "from": f"{SCENE_PATH.as_posix()}:2002",
                        "field": "itemPrefab",
                        "index": None,
                        "target_kind": "prefab",
                        "target_ref": "guidpath:Assets/Prefabs/Item.prefab",
                        "target_is_ui": False,
                    },
                ],
                "lifecycle_order": [],
            },
        },
    }


class TestEndToEndConvertScene:
    """End-to-end: ``convert_scene`` plumbs ``scene_runtime`` +
    ``scene_runtime_mode`` into ``convert_canvas`` so the carve-out
    fires only under generic."""

    def test_generic_end_to_end_drops_descendants(self, tmp_path):
        canvas = _canvas_with_controller_and_static_descendants()
        scene = _build_scene(tmp_path, canvas)

        place = convert_scene(
            parsed_scene=scene,
            unity_project_root=tmp_path,
            scene_runtime=_full_suppression_artifact(),
            scene_runtime_mode="generic",
        )

        assert len(place.screen_guis) == 1
        controller_host = _find_element(
            place.screen_guis[0].elements, "ControllerHost",
        )
        assert controller_host is not None
        assert controller_host.children == []

    def test_legacy_end_to_end_keeps_descendants(self, tmp_path):
        canvas = _canvas_with_controller_and_static_descendants()
        scene = _build_scene(tmp_path, canvas)

        # Default scene_runtime_mode="legacy" + populated artifact.
        place = convert_scene(
            parsed_scene=scene,
            unity_project_root=tmp_path,
            scene_runtime=_full_suppression_artifact(),
        )

        assert len(place.screen_guis) == 1
        controller_host = _find_element(
            place.screen_guis[0].elements, "ControllerHost",
        )
        assert controller_host is not None
        assert _find_element(controller_host.children, "StaticChild") is not None
        assert _find_element(
            controller_host.children, "StaticGrandchild",
        ) is not None

    def test_legacy_snapshot_unaffected_by_scene_runtime_argument(self, tmp_path):
        """Snapshot invariant: build the same UI scene twice under
        legacy — once with no artifact, once with the populated one —
        and assert the converted ScreenGui tree (recursive name list +
        per-element attributes) is identical. Pins the brief's
        "legacy emit byte-unchanged" guarantee for the UI path."""
        def _build() -> ParsedScene:
            return _build_scene(
                tmp_path, _canvas_with_controller_and_static_descendants(),
            )

        place_no_runtime = convert_scene(
            parsed_scene=_build(), unity_project_root=tmp_path,
        )
        place_with_runtime_legacy = convert_scene(
            parsed_scene=_build(), unity_project_root=tmp_path,
            scene_runtime=_full_suppression_artifact(),
            scene_runtime_mode="legacy",
        )

        def _snapshot(place) -> list[tuple[str, str, dict]]:
            out: list[tuple[str, str, dict]] = []
            for gui in place.screen_guis:
                out.append(("ScreenGui", gui.name, dict(gui.attributes)))
                stack: list = list(gui.elements)
                while stack:
                    e = stack.pop(0)
                    out.append((e.class_name, e.name, dict(e.attributes)))
                    stack[:0] = list(e.children)
            return out

        assert _snapshot(place_no_runtime) == _snapshot(
            place_with_runtime_legacy
        ), (
            "legacy UI emit must be byte-unchanged regardless of whether "
            "a scene_runtime artifact is threaded through"
        )
