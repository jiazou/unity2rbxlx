"""Tests for the pure dict/object accessor helpers in binary_scene_parser.

binary_scene_parser depends on UnityPy for the full parse path, but its small
PPtr/value extraction helpers are pure and were previously untested. They handle
both dict-shaped (parse_as_dict) and object-shaped (.read()) UnityPy results, so
both branches matter.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from unity.binary_scene_parser import _float, _get, _get_path_id


class TestGet:
    def test_dict_hit_and_default(self):
        assert _get({"a": 1}, "a") == 1
        assert _get({"a": 1}, "missing", "fallback") == "fallback"
        assert _get({}, "missing") is None

    def test_object_attr_and_default(self):
        obj = SimpleNamespace(a=7)
        assert _get(obj, "a") == 7
        assert _get(obj, "missing", -1) == -1


class TestGetPathId:
    def test_dict_m_pathid(self):
        assert _get_path_id({"m_PathID": 123}) == 123

    def test_dict_falls_back_through_aliases(self):
        assert _get_path_id({"path_id": 5}) == 5
        assert _get_path_id({"fileID": 9}) == 9

    def test_dict_zero_id_is_none(self):
        # A zero PPtr is a null reference, not object 0.
        assert _get_path_id({"m_PathID": 0}) is None

    def test_object_path_id_attr(self):
        assert _get_path_id(SimpleNamespace(path_id=42)) == 42

    def test_object_m_pathid_attr(self):
        assert _get_path_id(SimpleNamespace(m_PathID=8)) == 8

    def test_unrecognized_ref_is_none(self):
        assert _get_path_id(SimpleNamespace(other=1)) is None
        assert _get_path_id("not a ref") is None


class TestFloat:
    def test_dict_value_coerced(self):
        assert _float({"x": 3}, "x") == 3.0
        assert isinstance(_float({"x": 3}, "x"), float)

    def test_dict_string_numeric_coerced(self):
        assert _float({"x": "2.5"}, "x") == 2.5

    def test_dict_missing_uses_default(self):
        assert _float({}, "x") == 0.0
        assert _float({}, "x", 1.5) == 1.5

    def test_object_attr_coerced(self):
        assert _float(SimpleNamespace(x=4), "x") == 4.0
        assert _float(SimpleNamespace(), "x", -2.0) == -2.0
