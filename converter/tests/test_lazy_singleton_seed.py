"""Unit tests for the lazy-singleton build-time resolver (Phase 2 §1.1/§1.1a/§1.2).

Builds REALISTIC synthetic Unity ``.cs`` projects (mirroring trash-dash's
``CoroutineHandler.cs`` shape: a static self-typed backing field + a static
``instance`` getter that ``new GameObject`` + ``AddComponent<Self>`` + caches) so
the GuidIndex resolves through the canonical ``build_guid_index`` path — not
tautological synthetic records. The ``scene_runtime["modules"]`` rows are built
in the same GUID-keyed shape the planner emits (``stem`` / ``class_name`` /
``module_path`` / ``domain`` / ``runtime_bearing``).

Covers: a positive resolve (right backing_field + script_guid + module_path +
domain); the boot-safety ABSTAINs (non-trivial OnEnable / Start, getter doing
extra init, nontrivial field-initializer); detector keys on SHAPE not name; a
``runtime_bearing`` scene-placed singleton abstains; the dead-module exemption
keeps a seeded (would-be-dead) singleton module live. Also verifies the REAL
``CoroutineHandler.cs`` qualifies with backing_field ``m_Instance``.
"""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import pytest

from unity.guid_resolver import build_guid_index
from unity.script_analyzer import analyze_script
from converter.consumable_db_seed import build_base_by_class
from converter.lazy_singleton_seed import (
    LazySingletonSeed,
    passes_boot_safety_gate,
    resolve_lazy_singletons,
)


REAL_COROUTINE_HANDLER = Path(
    "/Users/jiazou/workspace/trash-dash/Assets/Scripts/CoroutineHandler.cs"
)


def _g(tag: str) -> str:
    """A distinct 32-char hex guid seeded by ``tag`` (leading letter → always a
    str when re-parsed from YAML, never coerced to an int)."""
    return "a" + hashlib.sha256(tag.encode()).hexdigest()[:31]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _write_meta(asset_path: Path, guid: str) -> None:
    asset_path.with_suffix(asset_path.suffix + ".meta").write_text(
        f"fileFormatVersion: 2\nguid: {guid}\n", encoding="utf-8",
    )


def _cs(root: Path, name: str, guid: str, source: str) -> None:
    """Write ``Assets/Scripts/<name>.cs`` + its ``.meta`` under ``root``."""
    p = root / "Assets" / "Scripts" / f"{name}.cs"
    _write(p, source)
    _write_meta(p, guid)


def _module_row(
    *,
    stem: str,
    class_name: str,
    module_path: str = "",
    domain: str = "server",
    runtime_bearing: bool = False,
) -> dict[str, object]:
    return {
        "stem": stem,
        "class_name": class_name,
        "module_path": module_path or f"ServerStorage.{stem}",
        "domain": domain,
        "runtime_bearing": runtime_bearing,
        "is_component_class": True,
    }


# A canonical CoroutineHandler-shaped lazy singleton (static self-typed field +
# self-instantiating getter, NO Awake/OnEnable/Start, benign DontDestroyOnLoad).
_COROUTINE_LIKE = """\
    using UnityEngine;
    using System.Collections;

    /// <summary>
    /// This class allows us to start Coroutines from non-Monobehaviour scripts
    /// Create a GameObject it will use to launch the coroutine on
    /// </summary>
    public class CoroutineHandler : MonoBehaviour
    {
        static protected CoroutineHandler m_Instance;
        static public CoroutineHandler instance
        {
            get
            {
                if(m_Instance == null)
                {
                    GameObject o = new GameObject("CoroutineHandler");
                    DontDestroyOnLoad(o);
                    m_Instance = o.AddComponent<CoroutineHandler>();
                }
                return m_Instance;
            }
        }
        public void OnDisable()
        {
            if(m_Instance)
                Destroy(m_Instance.gameObject);
        }
        static public Coroutine StartStaticCoroutine(IEnumerator coroutine)
        {
            return instance.StartCoroutine(coroutine);
        }
    }
"""


def _build(root: Path, classes: dict[str, tuple[str, str]]) -> tuple[
    object, dict[str, object], dict[str, str],
]:
    """Write each ``name -> (guid, source)`` ``.cs``, build the GuidIndex and the
    ``base_by_class`` map. Returns ``(guid_index, modules, guid_by_name)`` where
    ``modules`` is empty (the caller fills rows keyed by the guids)."""
    for name, (guid, src) in classes.items():
        _cs(root, name, guid, src)
    guid_index = build_guid_index(root)
    base_by_class = build_base_by_class(guid_index)
    guid_by_name = {name: guid for name, (guid, _src) in classes.items()}
    return guid_index, base_by_class, guid_by_name


def _resolve(root: Path, classes, rows):
    """Helper: build the project + modules dict and run the resolver. ``rows`` maps
    class-name -> module-row kwargs (without ``script_guid``, which the modules
    dict KEY supplies)."""
    guid_index, base_by_class, guid_by_name = _build(root, classes)
    modules: dict[str, object] = {}
    for name, row_kwargs in rows.items():
        modules[guid_by_name[name]] = _module_row(**row_kwargs)
    seeds = resolve_lazy_singletons(
        modules=modules,
        guid_index=guid_index,
        base_by_class=base_by_class,
        module_path_for_stem=lambda stem: (
            f"ServerStorage.{stem}" if stem else None
        ),
    )
    return seeds, guid_by_name


# --------------------------------------------------------------------------- #
# Positive case.
# --------------------------------------------------------------------------- #

def test_positive_coroutine_like_qualifies(tmp_path: Path) -> None:
    seeds, guid_by_name = _resolve(
        tmp_path,
        {"CoroutineHandler": (_g("ch"), _COROUTINE_LIKE)},
        {"CoroutineHandler": dict(
            stem="CoroutineHandler", class_name="CoroutineHandler",
            domain="server",
        )},
    )
    assert len(seeds) == 1
    seed = seeds[0]
    assert seed["class_stem"] == "CoroutineHandler"
    assert seed["backing_field"] == "m_Instance"
    assert seed["script_guid"] == guid_by_name["CoroutineHandler"]
    assert seed["module_path"] == "ServerStorage.CoroutineHandler"
    assert seed["domain"] == "server"


def test_real_coroutine_handler_cs_qualifies() -> None:
    """The REAL trash-dash CoroutineHandler.cs is the live target — verify the
    detector + boot-safety gate qualify it with backing_field ``m_Instance``."""
    if not REAL_COROUTINE_HANDLER.exists():
        pytest.skip("real trash-dash CoroutineHandler.cs not present")
    info = analyze_script(REAL_COROUTINE_HANDLER)
    assert info.class_name == "CoroutineHandler"
    assert info.lazy_singleton_field == "m_Instance"
    src = REAL_COROUTINE_HANDLER.read_text(encoding="utf-8")
    assert passes_boot_safety_gate(src, "CoroutineHandler", "m_Instance")


# --------------------------------------------------------------------------- #
# Detector keys on SHAPE, not the name.
# --------------------------------------------------------------------------- #

def test_detector_keys_on_shape_not_name(tmp_path: Path) -> None:
    """A differently-named class with the SAME shape + a different backing-field
    name still qualifies, and carries ITS field name (never a hardcoded
    m_Instance / CoroutineHandler)."""
    src = """\
        using UnityEngine;
        public class AudioManager : MonoBehaviour
        {
            static private AudioManager _instance;
            static public AudioManager Instance
            {
                get
                {
                    if(_instance == null)
                    {
                        GameObject o = new GameObject("AudioManager");
                        _instance = o.AddComponent<AudioManager>();
                    }
                    return _instance;
                }
            }
        }
    """
    seeds, _ = _resolve(
        tmp_path,
        {"AudioManager": (_g("am"), src)},
        {"AudioManager": dict(stem="AudioManager", class_name="AudioManager")},
    )
    assert len(seeds) == 1
    assert seeds[0]["class_stem"] == "AudioManager"
    assert seeds[0]["backing_field"] == "_instance"


# --------------------------------------------------------------------------- #
# ABSTAIN cases.
# --------------------------------------------------------------------------- #

def test_abstain_plain_singleton_no_lazy_create(tmp_path: Path) -> None:
    """A static field + getter that does NOT self-instantiate (no
    ``new GameObject`` + ``AddComponent``) is an ordinary singleton — abstain."""
    src = """\
        using UnityEngine;
        public class PlainSingleton : MonoBehaviour
        {
            static public PlainSingleton instance;
            void Awake() { instance = this; }
        }
    """
    seeds, _ = _resolve(
        tmp_path,
        {"PlainSingleton": (_g("ps"), src)},
        {"PlainSingleton": dict(stem="PlainSingleton", class_name="PlainSingleton")},
    )
    assert seeds == []


def test_abstain_runtime_bearing_scene_placed(tmp_path: Path) -> None:
    """A lazy-singleton-shaped class that is ALSO scene-placed (runtime_bearing)
    is already Awoken by the scene batch — abstain (don't double-construct)."""
    seeds, _ = _resolve(
        tmp_path,
        {"CoroutineHandler": (_g("ch2"), _COROUTINE_LIKE)},
        {"CoroutineHandler": dict(
            stem="CoroutineHandler", class_name="CoroutineHandler",
            runtime_bearing=True,
        )},
    )
    assert seeds == []


def test_abstain_nontrivial_onenable(tmp_path: Path) -> None:
    """A lazy singleton with a non-trivial C# OnEnable — addComponent fires
    OnEnable synchronously, so eager boot would run it early — abstain."""
    src = _COROUTINE_LIKE.replace(
        "    public void OnDisable()",
        "    public void OnEnable() { Application.targetFrameRate = 60; }\n"
        "    public void OnDisable()",
    )
    seeds, _ = _resolve(
        tmp_path,
        {"CoroutineHandler": (_g("ch3"), src)},
        {"CoroutineHandler": dict(
            stem="CoroutineHandler", class_name="CoroutineHandler",
        )},
    )
    assert seeds == []


def test_abstain_nontrivial_start(tmp_path: Path) -> None:
    """A lazy singleton with a non-trivial C# Start — addComponent schedules
    Start — abstain (timing-sensitive setup deferred to first-use in Unity)."""
    src = _COROUTINE_LIKE.replace(
        "    public void OnDisable()",
        "    void Start() { LoadConfig(); }\n"
        "    void LoadConfig() {}\n"
        "    public void OnDisable()",
    )
    seeds, _ = _resolve(
        tmp_path,
        {"CoroutineHandler": (_g("ch4"), src)},
        {"CoroutineHandler": dict(
            stem="CoroutineHandler", class_name="CoroutineHandler",
        )},
    )
    assert seeds == []


def test_abstain_getter_extra_init(tmp_path: Path) -> None:
    """A lazy-create getter that does EXTRA init (an ``.Init()`` call beyond the
    benign lazy-create statements) is side-effecting — abstain."""
    src = """\
        using UnityEngine;
        public class GameManagerSingleton : MonoBehaviour
        {
            static private GameManagerSingleton m_Instance;
            static public GameManagerSingleton instance
            {
                get
                {
                    if(m_Instance == null)
                    {
                        GameObject o = new GameObject("GM");
                        m_Instance = o.AddComponent<GameManagerSingleton>();
                        m_Instance.Init();
                    }
                    return m_Instance;
                }
            }
            void Init() {}
        }
    """
    seeds, _ = _resolve(
        tmp_path,
        {"GameManagerSingleton": (_g("gm"), src)},
        {"GameManagerSingleton": dict(
            stem="GameManagerSingleton", class_name="GameManagerSingleton",
        )},
    )
    assert seeds == []


def test_abstain_nontrivial_field_initializer(tmp_path: Path) -> None:
    """A lazy singleton with a nontrivial INSTANCE field initializer (``= new
    X()``) — that runs at construction time at boot — abstain."""
    src = """\
        using UnityEngine;
        using System.Collections.Generic;
        public class CacheSingleton : MonoBehaviour
        {
            private List<int> _cache = new List<int>();
            static private CacheSingleton m_Instance;
            static public CacheSingleton instance
            {
                get
                {
                    if(m_Instance == null)
                    {
                        GameObject o = new GameObject("Cache");
                        m_Instance = o.AddComponent<CacheSingleton>();
                    }
                    return m_Instance;
                }
            }
        }
    """
    seeds, _ = _resolve(
        tmp_path,
        {"CacheSingleton": (_g("cs"), src)},
        {"CacheSingleton": dict(
            stem="CacheSingleton", class_name="CacheSingleton",
        )},
    )
    assert seeds == []


def test_qualify_awake_assigns_only_backing_field(tmp_path: Path) -> None:
    """A lazy singleton WITH a C# Awake that assigns ONLY the backing field
    (``m_Instance = this;``) still qualifies (§1.1a)."""
    src = _COROUTINE_LIKE.replace(
        "    public void OnDisable()",
        "    void Awake() { m_Instance = this; }\n"
        "    public void OnDisable()",
    )
    seeds, _ = _resolve(
        tmp_path,
        {"CoroutineHandler": (_g("ch5"), src)},
        {"CoroutineHandler": dict(
            stem="CoroutineHandler", class_name="CoroutineHandler",
        )},
    )
    assert len(seeds) == 1


def test_abstain_awake_does_more_than_cache(tmp_path: Path) -> None:
    """A C# Awake that does MORE than the backing-field cache — abstain."""
    src = _COROUTINE_LIKE.replace(
        "    public void OnDisable()",
        "    void Awake() { m_Instance = this; DontDestroyOnLoad(gameObject); "
        "Subscribe(); }\n"
        "    void Subscribe() {}\n"
        "    public void OnDisable()",
    )
    seeds, _ = _resolve(
        tmp_path,
        {"CoroutineHandler": (_g("ch6"), src)},
        {"CoroutineHandler": dict(
            stem="CoroutineHandler", class_name="CoroutineHandler",
        )},
    )
    assert seeds == []


def test_abstain_no_client_server_domain(tmp_path: Path) -> None:
    """A lazy singleton whose module has no client/server domain — abstain (no
    side to seed it on)."""
    seeds, _ = _resolve(
        tmp_path,
        {"CoroutineHandler": (_g("ch7"), _COROUTINE_LIKE)},
        {"CoroutineHandler": dict(
            stem="CoroutineHandler", class_name="CoroutineHandler", domain="",
        )},
    )
    assert seeds == []


def test_empty_body_onenable_qualifies(tmp_path: Path) -> None:
    """A present-but-EMPTY C# OnEnable/Start does not fire timing-sensitive work,
    so it still qualifies (§1.1a — trivial body)."""
    src = _COROUTINE_LIKE.replace(
        "    public void OnDisable()",
        "    void OnEnable() {}\n    void Start() {}\n    public void OnDisable()",
    )
    seeds, _ = _resolve(
        tmp_path,
        {"CoroutineHandler": (_g("ch8"), src)},
        {"CoroutineHandler": dict(
            stem="CoroutineHandler", class_name="CoroutineHandler",
        )},
    )
    assert len(seeds) == 1


# --------------------------------------------------------------------------- #
# Determinism + dedup.
# --------------------------------------------------------------------------- #

def test_seeds_sorted_by_class_stem(tmp_path: Path) -> None:
    src_z = _COROUTINE_LIKE  # CoroutineHandler shape, renamed below
    src_a = _COROUTINE_LIKE.replace("CoroutineHandler", "AaaHandler")
    seeds, _ = _resolve(
        tmp_path,
        {
            "CoroutineHandler": (_g("zz"), src_z),
            "AaaHandler": (_g("aa"), src_a),
        },
        {
            "CoroutineHandler": dict(
                stem="CoroutineHandler", class_name="CoroutineHandler"),
            "AaaHandler": dict(stem="AaaHandler", class_name="AaaHandler"),
        },
    )
    assert [s["class_stem"] for s in seeds] == ["AaaHandler", "CoroutineHandler"]


# --------------------------------------------------------------------------- #
# Dead-module exemption (pipeline helper) — drives the REAL Pipeline.
# --------------------------------------------------------------------------- #

def _guid_for_stem(guid_index: object, stem: str) -> str:
    for g, e in getattr(guid_index, "guid_to_entry").items():
        ap = getattr(e, "asset_path", None)
        if ap is not None and ap.stem == stem and ap.suffix == ".cs":
            return g
    raise AssertionError(f"no guid for {stem}")


def test_dead_module_exemption_keeps_seeded_singleton_live(tmp_path: Path) -> None:
    """A would-be-dead (output-inert) singleton module is exempted from the dead
    set because the boot shim instantiates it. Drives the real
    ``Pipeline._lazy_singleton_live_module_names`` + ``_subphase_analyze_dead_modules``."""
    from converter.pipeline import Pipeline
    from core.roblox_types import RbxPlace, RbxScript

    # Build a real project + GuidIndex with the CoroutineHandler shape.
    _cs(tmp_path, "CoroutineHandler", _g("chx"), _COROUTINE_LIKE)
    guid_index = build_guid_index(tmp_path)
    guid = _guid_for_stem(guid_index, "CoroutineHandler")

    pipe = Pipeline(tmp_path, output_dir=tmp_path / "out", skip_upload=True)
    pipe.state.guid_index = guid_index
    pipe.state.rbx_place = RbxPlace()
    inert_body = "local M = {}\nfunction M.new() end\nreturn M\n"
    pipe.state.rbx_place.scripts = [
        RbxScript(
            name="CoroutineHandler", source=inert_body,
            script_type="ModuleScript", parent_path="ServerStorage",
        ),
    ]
    # The modules registry the lazy-singleton builder iterates (GUID-keyed).
    pipe.ctx.scene_runtime = {
        "modules": {
            guid: _module_row(
                stem="CoroutineHandler", class_name="CoroutineHandler",
                module_path="ServerStorage.CoroutineHandler", domain="server",
            ),
        },
    }

    live = pipe._lazy_singleton_live_module_names()
    assert "CoroutineHandler" in live

    # The resume (no-transpile) dead-module branch must NOT flag the inert
    # singleton: it is exempted as live-by-construction.
    pipe.state.transpilation_result = None
    pipe.ctx.dead_modules = ["CoroutineHandler"]  # persisted-dead from a prior run
    pipe._subphase_analyze_dead_modules()
    assert "CoroutineHandler" not in pipe.state.dead_modules
