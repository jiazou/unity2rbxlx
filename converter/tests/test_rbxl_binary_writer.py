"""
test_rbxl_binary_writer.py -- Unit tests for the XML-to-binary .rbxl converter.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from roblox.rbxl_binary_writer import MAGIC, xml_to_binary


MINIMAL_RBXLX = """\
<roblox xmlns:xmime="http://www.w3.org/2005/05/xmlmime" version="4">
  <Item class="Workspace" referent="RBX0">
    <Properties>
      <string name="Name">Workspace</string>
    </Properties>
    <Item class="Part" referent="RBX1">
      <Properties>
        <string name="Name">TestPart</string>
        <bool name="Anchored">true</bool>
        <float name="Transparency">0</float>
        <int name="BrickColor">194</int>
        <Vector3 name="Position">
          <X>1.5</X><Y>2.5</Y><Z>3.5</Z>
        </Vector3>
        <CoordinateFrame name="CFrame">
          <X>1.5</X><Y>2.5</Y><Z>3.5</Z>
          <R00>1</R00><R01>0</R01><R02>0</R02>
          <R10>0</R10><R11>1</R11><R12>0</R12>
          <R20>0</R20><R21>0</R21><R22>1</R22>
        </CoordinateFrame>
        <Color3uint8 name="Color3uint8">4294967295</Color3uint8>
      </Properties>
    </Item>
  </Item>
</roblox>
"""

SCRIPT_RBXLX = """\
<roblox xmlns:xmime="http://www.w3.org/2005/05/xmlmime" version="4">
  <Item class="Workspace" referent="RBX0">
    <Properties>
      <string name="Name">Workspace</string>
    </Properties>
    <Item class="Script" referent="RBX1">
      <Properties>
        <string name="Name">TestScript</string>
        <ProtectedString name="Source"><![CDATA[print("hello world")]]></ProtectedString>
      </Properties>
    </Item>
  </Item>
</roblox>
"""


class TestXmlToBinary:
    def test_produces_binary_file(self, tmp_path):
        xml_file = tmp_path / "test.rbxlx"
        xml_file.write_text(MINIMAL_RBXLX, encoding="utf-8")
        result = xml_to_binary(xml_file)
        assert result.exists()
        assert result.suffix == ".rbxl"

    def test_binary_has_magic_header(self, tmp_path):
        xml_file = tmp_path / "test.rbxlx"
        xml_file.write_text(MINIMAL_RBXLX, encoding="utf-8")
        result = xml_to_binary(xml_file)
        assert result.read_bytes()[:len(MAGIC)] == MAGIC

    def test_binary_is_not_empty(self, tmp_path):
        xml_file = tmp_path / "test.rbxlx"
        xml_file.write_text(MINIMAL_RBXLX, encoding="utf-8")
        result = xml_to_binary(xml_file)
        assert result.stat().st_size > len(MAGIC) + 10

    def test_custom_output_path(self, tmp_path):
        xml_file = tmp_path / "test.rbxlx"
        xml_file.write_text(MINIMAL_RBXLX, encoding="utf-8")
        out = tmp_path / "custom.rbxl"
        result = xml_to_binary(xml_file, out)
        assert result == out
        assert result.exists()

    def test_default_output_path(self, tmp_path):
        xml_file = tmp_path / "myplace.rbxlx"
        xml_file.write_text(MINIMAL_RBXLX, encoding="utf-8")
        result = xml_to_binary(xml_file)
        assert result.name == "myplace.rbxl"

    def test_sibling_emission_round_trip(self, tmp_path):
        """Pipeline emits .rbxl alongside .rbxlx with the same stem."""
        rbxlx = tmp_path / "converted_place.rbxlx"
        rbxlx.write_text(MINIMAL_RBXLX, encoding="utf-8")
        rbxl = xml_to_binary(rbxlx)
        assert rbxl.parent == rbxlx.parent
        assert rbxl.stem == rbxlx.stem
        assert rbxl.suffix == ".rbxl"


class TestBinaryWithScripts:
    def test_script_rbxlx_converts(self, tmp_path):
        xml_file = tmp_path / "test.rbxlx"
        xml_file.write_text(SCRIPT_RBXLX, encoding="utf-8")
        result = xml_to_binary(xml_file)
        data = result.read_bytes()
        assert data[:len(MAGIC)] == MAGIC
        assert b"hello world" in data


class TestTokenPropertyDefaults:
    """The binary format groups properties per class — if any Part has a
    ``Shape`` property, every Part needs a Shape value emitted. Parts
    without one get a default. The wrong default (0 = Ball) silently turns
    flat dock-style colliders into 1-stud spheres at runtime, only
    visible when the binary is loaded — the XML form loads fine because
    Studio uses Roblox's engine default (Block) for absent properties.
    """

    def test_missing_shape_defaults_to_block_not_ball(self, tmp_path):
        """Regression: a Part without ``<token name="Shape">`` must end up
        as Block in the binary, not Ball — even when *another* Part in the
        same class has a Shape property set."""
        xml = (
            '<?xml version="1.0"?>'
            '<roblox>'
            '<Item class="Workspace" referent="W">'
            '  <Item class="Part" referent="P1">'
            '    <Properties>'
            '      <string name="Name">FlatCollider</string>'
            '      <Vector3 name="size"><X>50</X><Y>1</Y><Z>10</Z></Vector3>'
            '    </Properties>'
            '  </Item>'
            '  <Item class="Part" referent="P2">'
            '    <Properties>'
            '      <string name="Name">SphereTrigger</string>'
            '      <Vector3 name="size"><X>2</X><Y>2</Y><Z>2</Z></Vector3>'
            '      <token name="Shape">0</token>'
            '    </Properties>'
            '  </Item>'
            '</Item>'
            '</roblox>'
        )
        xml_file = tmp_path / "shape_default.rbxlx"
        xml_file.write_text(xml, encoding="utf-8")
        result = xml_to_binary(xml_file)
        data = result.read_bytes()

        # We can't easily decode the full binary here without reimplementing
        # the reader, but we can assert the property-name-aware default
        # function works as intended at the unit level.
        from roblox.rbxl_binary_writer import (
            _default_for_property,
            _default_for_type,
            TYPE_ENUM,
        )
        # Shape default: Block (1), not Ball (0).
        assert _default_for_property("Shape", TYPE_ENUM) == 1
        # Type default unchanged for non-name-overridden enums.
        assert _default_for_type(TYPE_ENUM) == 0
        # An unknown token property still falls back to the type default.
        assert _default_for_property("SomeUnknownToken", TYPE_ENUM) == 0


class TestBinaryErrorHandling:
    def test_nonexistent_file(self, tmp_path):
        with pytest.raises((FileNotFoundError, ET.ParseError, OSError)):
            xml_to_binary(tmp_path / "nope.rbxlx")

    def test_empty_xml(self, tmp_path):
        xml_file = tmp_path / "empty.rbxlx"
        xml_file.write_text("", encoding="utf-8")
        with pytest.raises((ET.ParseError, Exception)):
            xml_to_binary(xml_file)

    def test_malformed_xml(self, tmp_path):
        xml_file = tmp_path / "bad.rbxlx"
        xml_file.write_text("<roblox><Item>unclosed", encoding="utf-8")
        with pytest.raises((ET.ParseError, Exception)):
            xml_to_binary(xml_file)
