# Toolchanger kits

Kit-specific glue between EddySeek and how a printer loads tools and stores XY offsets.

| Module        | Role                                           |
| ------------- | ---------------------------------------------- |
| `protocol.py` | `ToolProtocol` + `ToolAlignConfig` ABC         |
| `generic.py`  | `GenericTool` + `GenericToolAlignConfig`       |
| `indx.py`     | `IndxTool` + `IndxToolAlignConfig`             |
| `types.py`    | Registry + `tool_align_from_config`            |

Select the kit with `toolchanger_type` in `[eddy_seek]` (default `generic`).

## Built-in types

**`generic`** - generic toolchanger. Offsets live in `[es_Tn]` autosave sections with optional `manual_adjust_x/y`. Apply via `SET_GCODE_OFFSET`; wire `EDDY_SEEK_APPLY_OFFSET` into your own macros. You load tools yourself before `EDDY_SEEK_TOOL`.

**`indx`** - [Bondtech INDX](https://github.com/BondtechAB/INDX) macros. Load via `CHANGE_TOOL` before alignment. Persists XY to `SAVE_VARIABLE` (`t{n}_offset_x/y`). Tool count from `gcode_macro TOOL_POSITIONS`. Do **not** use `EDDY_SEEK_APPLY_OFFSET` - `CHANGE_TOOL` applies XY from save variables. See `indx.py` module docstring for upstream macro contracts.

## Auto-detection

Only **indx** fingerprints the printer config (`[indx]` section via `suggest_for_config`). If that matches and your active `toolchanger_type` is not `indx`, EddySeek queues a warning via `KConsole.queue` (flushed on the first G-code command). It never changes config for you.

Default `toolchanger_type` is `generic`; `generic` is not auto-detected.

## Adding a kit

1. Add `tools/<name>.py` with a `Tool` dataclass implementing `ToolProtocol` and a `ToolAlignConfig` subclass.
2. Register in `registry.toolchanger_types()` (see `registry.py`).
3. Import the module from `types.py` so the registry is populated.
4. Add tests in `tests/test_toolchangers.py`.
