"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

from pprint import pformat
from typing import TYPE_CHECKING, Any, ClassVar, cast

from fakes import (
    CommandError,
    FakeGcmd,
    FakeGcode,
    FakeKlipperConfig,
    FakePrinter,
    as_config,
)
from pytest import raises

from eddy_seek.common import Offset
from eddy_seek.host import EddySeek
from eddy_seek.tools.diy import (
    DiyTool,
    DiyToolAlignConfig,
)
from eddy_seek.tools.indx import (
    IndxTool,
    IndxToolAlignConfig,
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


class _EmptySaveVars:
    allVariables: ClassVar[dict[str, float]] = {}


class _PrefixSection:
    def __init__(self, name: str) -> None:
        self._name = name

    def get_name(self) -> str:
        return self._name


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

    def getfloat(self, key: str, default: float = 0.0, **kwargs: Any) -> float:
        if key not in self._options:
            return default
        return float(self._options[key])

    def getboolean(self, key: str, default: bool = False, **kwargs: Any) -> bool:
        if key not in self._options:
            return default
        val = self._options[key]
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("1", "true", "yes", "on")

    def get_prefix_options(self, prefix: str) -> dict[str, str]:
        return {k: str(v) for k, v in self._options.items() if k.startswith(prefix)}


class _MainConfig:
    def __init__(self, sections: dict[str, dict[str, Any]] | None = None) -> None:
        self._sections = sections or {}

    def has_section(self, section: str) -> bool:
        return section in self._sections

    def getsection(self, section: str) -> _MainConfigSection:
        return _MainConfigSection(self._sections[section])

    def get_prefix_sections(self, prefix: str) -> list[_PrefixSection]:
        return [
            _PrefixSection(name) for name in self._sections if name.startswith(prefix)
        ]


def _main(sections: dict[str, dict[str, Any]] | None = None) -> ConfigWrapper:
    return cast(Any, _MainConfig(sections))


def _diy_tool_section(**overrides: Any) -> dict[str, Any]:
    section = {
        "offset_x": 0.0,
        "offset_y": 0.0,
        "manual_adjust_x": 0.0,
        "manual_adjust_y": 0.0,
        "is_calibrated": False,
    }
    section.update(overrides)
    return section


def _diy_main(*tool_numbers: int) -> ConfigWrapper:
    return _main({f"es_T{n}": _diy_tool_section() for n in tool_numbers})


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
    with_save_variables: bool | None = None,
    **options: Any,
) -> ToolAlignConfig:
    main_config = main or _main()
    cf = configfile or _Configfile(main_config)
    g = gcode or FakeGcode()
    if with_save_variables is None:
        with_save_variables = options.get("toolchanger_type") == "indx"

    def get_printer():
        objects: dict[str, Any] = {"gcode": g, "configfile": cf}
        if with_save_variables:
            objects["save_variables"] = _EmptySaveVars()
        return FakePrinter(**objects)

    return tool_align_from_config(
        as_config(
            FakeKlipperConfig(
                get_printer=get_printer,
                **options,
            )
        )
    )


def test_diy_starts_empty_without_sections():
    cfg = _tool_config()
    assert isinstance(cfg, DiyToolAlignConfig)
    assert cfg.tools == []
    assert cfg.tool_count == 0


def test_diy_discovers_sections_with_gap():
    main = _main(
        {
            "es_T0": _diy_tool_section(is_calibrated=True),
            "es_T2": _diy_tool_section(offset_x=1.0),
        }
    )
    cfg = _tool_config(main=main)
    assert cfg.tool_count == 3
    assert cfg.tools[0].is_calibrated is True
    assert cfg.tools[1].is_calibrated is False
    assert cfg.tools[2].offset.x == 1.0


def test_diy_get_tool_grows_list():
    cfg = _tool_config()
    assert isinstance(cfg, DiyToolAlignConfig)
    tool = cfg.get_tool(3)
    assert tool.tool_number == 3
    assert cfg.tool_count == 4
    assert len(cfg.tools) == 4


def test_diy_apply_offset_set_gcode_only():
    gcode = FakeGcode()
    cfg = _tool_config(gcode=gcode, main=_diy_main(0, 1))
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
    main = _diy_main(0, 1)
    configfile = _Configfile(main)
    cfg = _tool_config(gcode=gcode, main=main, configfile=configfile)
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


def test_apply_offset_errors_when_unsupported():
    class _Host:
        def __init__(self) -> None:
            self._tools = type(
                "_Tools",
                (),
                {"supports_apply_offset": staticmethod(lambda: False)},
            )()

    with raises(CommandError, match="not used for this toolchanger kit"):
        EddySeek.cmd_EDDY_SEEK_APPLY_OFFSET(_Host(), FakeGcmd())


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


def test_indx_rejects_partial_save_variables():
    with raises(ValueError, match=r"incomplete INDX offsets.*t1_offset_y"):
        IndxToolAlignConfig._load_tool_or_default({"t1_offset_x": 0.25}, 1)
    with raises(ValueError, match=r"incomplete INDX offsets.*t1_offset_x"):
        IndxToolAlignConfig._load_tool_or_default({"t1_offset_y": -0.5}, 1)


def test_indx_requires_save_variables_module():
    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 2}})
    with raises(ValueError, match=r"requires \[save_variables\]"):
        _tool_config(main=main, toolchanger_type="indx", with_save_variables=False)


def test_indx_persist_hint():
    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 2}})
    cfg = _tool_config(main=main, toolchanger_type="indx")
    assert "save_variables" in cfg.persist_hint()
    diy = _tool_config()
    assert "SAVE_CONFIG" in diy.persist_hint()


def test_indx_rejects_diy_only_tool_prefix():
    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 3}})
    with raises(ValueError, match="does not use tool_prefix"):
        _tool_config(main=main, toolchanger_type="indx", tool_prefix="es_T")


def test_detect_toolchanger_types_indx_from_indx_section():
    main = _main({_INDX: {"mcu": "indxmcu"}})
    detected = detect_toolchanger_types(main)
    assert detected == ["indx"]


def test_detect_toolchanger_types_ignores_tool_positions_without_indx():
    main = _main({_TOOL_POSITIONS: {"variable_tool_count": 2}})
    assert detect_toolchanger_types(main) == []


def test_tool_align_suggests_indx_when_diy_active(caplog):
    import logging

    from eddy_seek.kconsole import KConsole

    KConsole.clear_queue()
    main = _main(
        {
            _INDX: {"mcu": "indxmcu"},
            _TOOL_POSITIONS: {"variable_tool_count": 2},
        }
    )
    with caplog.at_level(logging.WARNING):
        cfg = _tool_config(main=main, toolchanger_type="diy")
    assert cfg.toolchanger_type == "diy"
    pending = KConsole.pending()
    assert pending
    assert any(
        msg_type == "warn"
        and "suggests toolchanger_type: indx" in msg
        and "[indx] section in config" in msg
        and "set toolchanger_type: indx in [eddy_seek]" in msg
        for msg_type, msg in pending
    )
    assert any(
        "suggests toolchanger_type: indx" in r.message
        and "[indx] section in config" in r.message
        for r in caplog.records
    )
    KConsole.clear_queue()


def test_unknown_toolchanger_type_rejected():
    with raises(ValueError, match="unknown toolchanger_type"):
        _tool_config(toolchanger_type="stealth")


def test_indx_without_tool_positions_errors():
    with raises(ValueError, match="TOOL_POSITIONS"):
        _tool_config(toolchanger_type="indx")


def test_tool_align_config_diy_save_and_apply():
    gcode = FakeGcode()
    main = _diy_main(0, 1)
    configfile = _Configfile(main)
    cfg = _tool_config(
        gcode=gcode,
        main=main,
        configfile=configfile,
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
    diy = _tool_config(main=_diy_main(0, 1))
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


def test_eddy_seek_status_dumps_get_status():
    captured: list[str] = []

    class _Console:
        def info(self, msg: str) -> None:
            captured.append(msg)

    class _Host:
        def get_status(self, eventtime: float) -> dict[str, Any]:
            return {
                "toolchanger_type": "diy",
                "last_freq": 42.0,
                "tools": {},
            }

        def refresh_console(self, gcmd: FakeGcmd) -> _Console:
            return _Console()

    EddySeek.cmd_EDDY_SEEK_STATUS(_Host(), FakeGcmd())
    assert captured == [pformat(_Host().get_status(0))]
