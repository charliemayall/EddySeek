"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, cast

from fakes import FakeGcode, FakeKlipperConfig, FakePrinter, as_config
from pytest import LogCaptureFixture, raises

from eddy_seek.common import Offset
from eddy_seek.tools.diy import (
    DiyTool,
    DiyToolAlignConfig,
)
from eddy_seek.tools.indx import (
    IndxTool,
    ToolAlignConfig,
)
from eddy_seek.tools.types import (
    detect_toolchanger_types,
    tool_align_from_config,
)

if TYPE_CHECKING:
    from klippy.configfile import ConfigWrapper

_TOOL_POSITIONS = "gcode_macro TOOL_POSITIONS"
_INDX = "indx"


class _MainConfigSection:
    def __init__(self, options: dict[str, Any]) -> None:
        self._options = options

    def get(self, key: str, default: Any = None) -> Any:
        if key not in self._options:
            return default
        return self._options[key]

    def getint(self, key: str, default: int = 0, **kwargs: Any) -> int:
        if key not in self._options:
            return default
        return int(self._options[key])

    def get_prefix_options(self, prefix: str) -> dict[str, str]:
        return {k: str(v) for k, v in self._options.items() if k.startswith(prefix)}


class _MainConfig:
    def __init__(self, sections: dict[str, dict[str, Any]] | None = None) -> None:
        self._sections = sections or {}

    def has_section(self, section: str) -> bool:
        return section in self._sections

    def getsection(self, section: str) -> _MainConfigSection:
        return _MainConfigSection(self._sections[section])


def _main(sections: dict[str, dict[str, Any]] | None = None) -> ConfigWrapper:
    return cast(Any, _MainConfig(sections))


class _Configfile:
    def __init__(self, main: ConfigWrapper | _MainConfig) -> None:
        self._main = main
        self.removed: list[str] = []
        self.sets: list[tuple[str, str, str]] = []

    def read_main_config(self) -> ConfigWrapper | _MainConfig:
        return self._main

    def remove_section(self, section: str) -> None:
        self.removed.append(section)

    def set(self, section: str, option: str, value: str) -> None:
        self.sets.append((section, option, value))


class _ConfigfilePrinter:
    def __init__(
        self, main: ConfigWrapper | _MainConfig, gcode: FakeGcode | None = None
    ) -> None:
        self._main = main
        self._gcode = gcode or FakeGcode()

    def lookup_object(self, name: str):
        if name == "configfile":
            return _Configfile(self._main)
        if name == "gcode":
            return self._gcode
        raise KeyError(name)


def _tool_config(
    *,
    main: ConfigWrapper | None = None,
    gcode: FakeGcode | None = None,
    configfile: _Configfile | None = None,
    **options: Any,
) -> ToolAlignConfig:
    main_config = main or _main()
    cf = configfile or _Configfile(main_config)
    g = gcode or FakeGcode()

    def get_printer():
        return FakePrinter(gcode=g, configfile=cf)

    return tool_align_from_config(
        as_config(
            FakeKlipperConfig(
                get_printer=get_printer,
                **options,
            )
        )
    )


def test_diy_apply_offset_set_gcode_only():
    gcode = FakeGcode()
    cfg = _tool_config(gcode=gcode, tool_count=2)
    tool = DiyTool(
        tool_number=1,
        offset=Offset(1.5, -0.5),
        manual_offset=Offset.zero(),
        is_calibrated=True,
    )
    cfg.tools[1] = tool
    cfg.apply_tool_offset(1)
    assert gcode.scripts == ["SET_GCODE_OFFSET X=1.5 Y=-0.5"]


def test_diy_save_tool_stages_es_tn():
    gcode = FakeGcode()
    main = _main()
    configfile = _Configfile(main)
    cfg = _tool_config(gcode=gcode, main=main, configfile=configfile, tool_count=2)
    tool = DiyTool(
        tool_number=1,
        offset=Offset(1.0, 2.0),
        manual_offset=Offset.zero(),
        is_calibrated=True,
    )
    cfg.tools[1] = tool
    cfg.save_tool(tool)
    assert gcode.scripts == []
    assert "es_T1" in configfile.removed
    assert any(s[0] == "es_T1" for s in configfile.sets)


def test_diy_does_not_warn_on_tool_count(caplog: LogCaptureFixture):
    with caplog.at_level(logging.WARNING):
        _tool_config(tool_count=4)
    assert caplog.records == []


def test_indx_tool_count_from_tool_positions():
    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 3}})
    cfg = _tool_config(main=main, toolchanger_type="indx")
    assert cfg.tool_count == 3


def test_indx_save_tool_save_variable_only():
    gcode = FakeGcode()
    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 2}})
    configfile = _Configfile(main)
    cfg = _tool_config(
        main=main, gcode=gcode, configfile=configfile, toolchanger_type="indx"
    )
    tool = IndxTool(
        tool_number=1,
        offset=Offset(0.1, -0.2),
        is_calibrated=True,
    )
    cfg.tools[1] = tool
    cfg.save_tool(tool)
    assert gcode.scripts == [
        "SAVE_VARIABLE VARIABLE=t1_offset_x VALUE=0.100000",
        "SAVE_VARIABLE VARIABLE=t1_offset_y VALUE=-0.200000",
    ]
    assert configfile.removed == []
    assert configfile.sets == []


def test_indx_supports_apply_offset_false():
    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 2}})
    cfg = _tool_config(main=main, toolchanger_type="indx")
    assert cfg.supports_apply_offset() is False


def test_indx_hydrates_save_variables():
    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 2}})

    class _SaveVars:
        allVariables: ClassVar[dict[str, float]] = {
            "t1_offset_x": 0.25,
            "t1_offset_y": -0.5,
        }

    g = FakeGcode()
    cf = _Configfile(main)

    def get_printer():
        return FakePrinter(gcode=g, configfile=cf, save_variables=_SaveVars())

    cfg = tool_align_from_config(
        as_config(
            FakeKlipperConfig(
                get_printer=get_printer,
                toolchanger_type="indx",
            )
        )
    )
    assert cfg.tools[0].is_calibrated is False
    assert cfg.tools[1].is_calibrated is True
    assert cfg.tools[1].offset == Offset(0.25, -0.5)


def test_indx_persist_hint():
    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 2}})
    cfg = _tool_config(main=main, toolchanger_type="indx")
    assert "save_variables" in cfg.persist_hint()
    diy = _tool_config(tool_count=1)
    assert "SAVE_CONFIG" in diy.persist_hint()


def test_indx_rejects_diy_only_keys():
    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 3}})
    with raises(ValueError, match="does not use tool_count"):
        _tool_config(main=main, toolchanger_type="indx", tool_count=4)
    with raises(ValueError, match="does not use tool_prefix"):
        _tool_config(main=main, toolchanger_type="indx", tool_prefix="es_T")


def test_detect_toolchanger_types_indx_from_indx_section():
    main = _main({_INDX: {"mcu": "indxmcu"}})
    detected = detect_toolchanger_types(main)
    assert detected == ["indx"]


def test_detect_toolchanger_types_ignores_tool_positions_without_indx():
    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 2}})
    assert detect_toolchanger_types(main) == []


def test_tool_align_suggests_indx_when_diy_active(caplog: LogCaptureFixture):
    main = _main(
        {
            _INDX: {"mcu": "indxmcu"},
            _TOOL_POSITIONS: {"variable_tool_count": 2},
        }
    )
    with caplog.at_level(logging.INFO):
        cfg = _tool_config(main=main, toolchanger_type="diy")
    assert cfg.toolchanger_type == "diy"
    assert any(
        "suggests toolchanger_type: indx" in r.message
        and "[indx] section in config" in r.message
        for r in caplog.records
    )


def test_unknown_toolchanger_type_rejected():
    with raises(ValueError, match="unknown toolchanger_type"):
        _tool_config(toolchanger_type="stealth")


def test_indx_without_tool_positions_errors():
    with raises(ValueError, match="TOOL_POSITIONS"):
        _tool_config(toolchanger_type="indx")


def test_tool_align_config_diy_save_and_apply():
    gcode = FakeGcode()
    main = _main()
    configfile = _Configfile(main)
    cfg = _tool_config(
        gcode=gcode,
        main=main,
        configfile=configfile,
        tool_count=2,
    )
    assert isinstance(cfg, DiyToolAlignConfig)
    tool = DiyTool(
        tool_number=1,
        offset=Offset(0.5, 0.25),
        manual_offset=Offset.zero(),
        is_calibrated=True,
    )
    cfg.tools[1] = tool
    gcode.scripts.clear()
    cfg.save_tool(tool)
    assert gcode.scripts == []
    assert "es_T1" in configfile.removed
    cfg.apply_tool_offset(1)
    assert gcode.scripts == ["SET_GCODE_OFFSET X=0.5 Y=0.25"]


def test_tool_align_config_indx_wires_through():
    gcode = FakeGcode()
    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 2}})
    configfile = _Configfile(main)
    cfg = _tool_config(
        main=main, gcode=gcode, configfile=configfile, toolchanger_type="indx"
    )
    assert cfg.tool_count == 2
    tool = IndxTool(
        tool_number=1,
        offset=Offset(0.3, 0.4),
        is_calibrated=True,
    )
    cfg.tools[1] = tool
    gcode.scripts.clear()
    cfg.save_tool(tool)
    assert gcode.scripts[0].startswith("SAVE_VARIABLE VARIABLE=t1_offset_x")
    assert configfile.sets == []
    assert cfg.supports_apply_offset() is False


def test_status_keys_diy_vs_indx():
    diy = _tool_config(tool_count=2)
    assert diy.tool_status_key(1) == "es_T1"
    assert "es_T1" in diy.status_tools()
    assert diy.kit_trace()["tool_prefix"] == "es_T"

    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 2}})
    indx = _tool_config(main=main, toolchanger_type="indx")
    assert indx.tool_status_key(1) == "t1"
    assert "t1" in indx.status_tools()
    assert indx.kit_trace() == {}
    indx_tool = IndxTool(
        tool_number=1,
        offset=Offset(0.1, 0.2),
        is_calibrated=True,
    )
    assert "manual_offset" not in indx_tool.to_dict()
