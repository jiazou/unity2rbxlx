"""
prefab_id.py -- Single canonical prefab-id construction (Slice 1.2 / D11).

The prefab ``prefab_id`` join key is produced in three places: the scene-runtime
planner (subplan key), the scene converter (converter-time stamp), and the
addressables resolver (``by_address``/``by_label`` -> prefab_id). They MUST agree
byte-for-byte or the resolver's addressable ids point at keys the host's
``_plan.prefabs`` never holds. This module holds the ONE implementation all three
delegate to (D6c / AC14).
"""

from __future__ import annotations

from pathlib import Path


def canonical_prefab_id(
    guid: str,
    abs_path: Path | None,
    project_root: Path | None,
) -> str:
    """Build the canonical ``prefab_id`` for a prefab template.

    - ``project_root is None`` -> ``guid`` (or ``""`` when no guid).
    - ``abs_path`` resolves OUTSIDE ``project_root`` (or is ``None``) -> ``""``
      (conservative "skip stamping"; never leak an absolute path).
    - otherwise -> ``"<guid>:<project-relative-path>"`` when a guid is known,
      else the bare project-relative path.

    The project-relative path is always forward-slashed (``as_posix``) so JSON
    ids round-trip identically across platforms.
    """
    if project_root is None:
        return guid if guid else ""
    if abs_path is None:
        return ""
    try:
        rel = (
            abs_path.resolve()
            .relative_to(project_root.resolve())
            .as_posix()
        )
    except ValueError:
        return ""
    return f"{guid}:{rel}" if guid else rel
