"""Slice T-bullet (AC c): the generated ``clonePrefabTemplate`` defaults a nil
parent to ``workspace`` so a runtime-spawned bullet enters the DataModel.

Drives the REAL autogen codegen functions (client + server entrypoints — the
two places that emit ``clonePrefabTemplate``) and asserts on their generated
Luau ``source``. The expected Luau is NOT hand-written; it is generated and
the assertions run over the output string.
"""

from converter.autogen import (
    generate_scene_runtime_client_entrypoint,
    generate_scene_runtime_server_entrypoint,
)

_OLD_FORM = "if parent then clone.Parent = parent end"
_NEW_FORM = "clone.Parent = parent or workspace"


def test_client_entrypoint_clone_defaults_parent_to_workspace() -> None:
    source = generate_scene_runtime_client_entrypoint().source
    assert _NEW_FORM in source
    assert _OLD_FORM not in source


def test_server_entrypoint_clone_defaults_parent_to_workspace() -> None:
    source = generate_scene_runtime_server_entrypoint().source
    assert _NEW_FORM in source
    assert _OLD_FORM not in source


def test_both_clone_sites_use_workspace_default() -> None:
    """Both emitted entrypoints carry the new form and neither carries the old."""
    sources = [
        generate_scene_runtime_client_entrypoint().source,
        generate_scene_runtime_server_entrypoint().source,
    ]
    combined = "\n".join(sources)
    assert combined.count(_NEW_FORM) == 2
    assert _OLD_FORM not in combined
