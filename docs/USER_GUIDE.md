# EddySeek User Guide

EddySeek is a Klipper extra for **nozzle alignment on toolchanger printers** using an
LDC1612 eddy-current sensor. It reads live coil frequency, runs XY search routines,
and measures per-tool offsets relative to a reference nozzle.

## Does it work?

Using `EDDY_SEEK_ACCURACY MOCK=1 REPEATS=250`

- Average duration **~7.3 s** per repeat
- Mean difference between result and reference center - X=+0.01 Y=+0.01 mm
- σ X=0.021 Y=0.014 mm
- max scatter 0.047mm

---

## What you need

- Klipper or Kalico
- An LDC1612 eddy-current probe (dedicated to nozzle alignment - not your bed-mesh probe)
- A toolchanger or multi-nozzle setup
- G-code macros to load each tool (`T0`, `T1`, …)

---

## Install

```bash
cd ~
git clone https://github.com/charliemayall/EddySeek.git
cd EddySeek
./install.sh
```

Non-default Klipper path:

```bash
./install.sh ~/my_non_standard_dir/klipper/klippy/extras
```

**Moonraker update-manager** - add to `moonraker.conf`, run `./install.sh` once:

```ini
[update_manager eddy_seek]
type: git_repo
path: ~/EddySeek
origin: https://github.com/charliemayall/EddySeek.git
primary_branch: main
channel: stable
managed_services: klipper
is_system_service: False
post_update_script: install.sh
```

Add `[eddy_seek]` to `printer.cfg`, then `FIRMWARE_RESTART`.

---

## Hardware and sensor setup

Configure the LDC1612 inside `[eddy_seek]` (separate from your bed-mesh probe):

```ini
[eddy_seek]
sensor_type: ldc1612
i2c_address: 42
i2c_mcu: mcu
i2c_bus: i2c1
sensor_x: 150.0   # machine XY of coil - rough is fine
sensor_y: 150.0
sensor_z: 5.0     # optional - seek commands error if machine Z is outside [sensor_z, sensor_z + 0.25] mm
```

Optional LDC1612 tuning keys (`frequency`, `max_sensor_hz`, `reg_drive_current`, …) can live here too.

## ⚠️⚠️⚠️ IMPORTANT ⚠️⚠️⚠️

- Load Macros must put the toolhead in a position where it is safe to move X to tool 0's center, and then Y to tool 0's center. Do not use EDDY_SEEK_TOOLS or EDDY_SEEK_TOOL LOAD=1 if you can not guarantee this.

## Minimal calibration workflow

Generic toolchanger: see [Minimal calibration workflow](#minimal-calibration-workflow) for a more detailed workflow.

Bondtech INDX: see [Bondtech INDX](#bondtech-indx-toolchanger_type-indx)

### Generic toolchanger (default)

1. Install EddySeek (see [Install](#install)).
2. Add `[eddy_seek]` to `printer.cfg`, or `[include eddy_seek.cfg]` in `printer.cfg`
3. Configure I2C settings, `sensor_x` / `sensor_y` / `sensor_z`, `tool_count`, and `load_tool_macro_prefix` (default `T`).
4. Ensure `T0`, `T1`, … (or your custom load macros) exist.
5. `FIRMWARE_RESTART`
6. `EDDY_SEEK_QUERY` - confirm you are getting samples.
7. Load Tool 0, park at probe height above the sensor at sensor_x, sensor_y.
8. `EDDY_SEEK_START` - you should see the toolhead align with the sensor from its current position. If the toolhead is not aligned, you should adjust `sensor_x` and `sensor_y` to be closer to the toolhead.
9. Repeat step 8 until you very little to no offset found, this is a good position to set as your `sensor_x` and `sensor_y`.
10. Keep Tool 0 loaded, and run `EDDY_SEEK_TOOL TOOL=0`
11. Calibrate each tool by running `EDDY_SEEK_TOOL TOOL=n`, use `LOAD=1` to have EddySeek load the tool for you.
12. `SAVE_CONFIG` - persist offsets in `es_Tn` sections in `printer.cfg`.
13. Add `EDDY_SEEK_APPLY_OFFSET TOOL=n` to your toolchange macros or slicer
14. Done!

### Bondtech INDX (`toolchanger_type: indx`)

1. Install Bondtech INDX macros per their docs (see [Bondtech INDX](https://github.com/BondtechAB/INDX))
2. In `printer.cfg`, include your `eddy_seek.cfg` file **after** your INDX macro files.
3. Configure your `[eddy_seek]` section with I2C settings, `sensor_x` / `sensor_y` / `sensor_z (optional)`, and `toolchanger_type: indx`.
4. Do **not** set `tool_count`, `load_tool_macro_prefix`, or `tool_prefix`.
5. `FIRMWARE_RESTART`
6. `EDDY_SEEK_QUERY` - confirm samples increment.
7. Home, park at probe height above the sensor (EddySeek does not move Z).
8. `EDDY_SEEK_TOOLS` - runs `CHANGE_TOOL` per tool; offsets save to `save_variables` (`t{n}_offset_x` / `t{n}_offset_y`).
9. Use INDX `CAL_Z` for Z offsets. Do **not** add `EDDY_SEEK_APPLY_OFFSET` to INDX macros - `CHANGE_TOOL` applies XY (and Z from `CAL_Z`) at print time.

EddySeek errors at config load if DIY-only keys (`tool_count`, `load_tool_macro_prefix`, `tool_prefix`) are present with `toolchanger_type: indx`.

On startup, EddySeek may log an **info** line suggesting `toolchanger_type: indx` when it finds INDX macros. It will not change your config automatically - set `toolchanger_type: indx` in `[eddy_seek]` yourself.

**DIY vs INDX at a glance**

|                | DIY                                     | INDX                         |
| -------------- | --------------------------------------- | ---------------------------- |
| Tool count     | `tool_count` in `[eddy_seek]`           | `gcode_macro TOOL_POSITIONS` |
| Load macro     | `T0`, `T1`, …                           | `CHANGE_TOOL`                |
| Saved offsets  | `[es_Tn]` in `printer.cfg`              | `save_variables`             |
| Apply at print | `EDDY_SEEK_APPLY_OFFSET` in your macros | built into INDX pickup       |

---

## Configuration reference

See [example.cfg](../example.cfg) (DIY), [example_indx.cfg](../example_indx.cfg) (Bondtech INDX), or [example_minimal.cfg](../example_minimal.cfg) (autodetect starter).

### `[eddy_seek]` - main options

<!-- BEGIN:seek-config-main -->
| Option | Default | Description |
| ------ | ------- | ----------- |
| `sensor_type` | _(required)_ | `ldc1612` |
| `i2c_address` | `42` | LDC1612 I2C address (`0x2a`) |
| `i2c_mcu` | _(required)_ | MCU name, e.g. `mcu` |
| `i2c_bus` | _(required)_ | I2C bus, e.g. `i2c1` |
| `tool_count` | `1` | Number of tools (DIY only; config error if set with `toolchanger_type: indx`) |
| `toolchanger_type` | `diy` | `diy` or `indx` - INDX uses `CHANGE_TOOL` and `TOOL_POSITIONS` |
| `tool_prefix` | `es_T` | Prefix for saved offset sections (`es_T1`, …) |
| `load_tool_macro_prefix` | `T` | Prefix for load macros (`T0`, `T1`, …; DIY only) |
| `sensor_x` / `sensor_y` | _(required)_ | Machine XY of sensor coil; tool 0 jogs here before seeking |
| `sensor_z` | _(optional)_ | Machine Z for seek commands; errors if outside `[sensor_z, sensor_z + 0.25]` mm |
| `max_jog_x` / `max_jog_y` | `2.5` | Max search radius from start (mm) |
| `tolerance` | `0.05` | Stop when both axes move less than this (mm) |
| `dwell_time` | `0.5` | Seconds at each probe point (grid strategies only) |
| `jog_speed` | `80` | Feedrate for search jogs (mm/s) |
| `search_for` | `max` | Which frequency extreme marks the nozzle centre (`max` for most users) |
| `strategy` | `sweep_centroid` | `sweep_centroid`, `centroid`, or `debug_scan` (diag only) |
| `max_passes` | `6` | Search passes before giving up |
| `save_session_trace` | `False` | Write probe JSON to `result_folder` (debug) |
| `save_plots` | `False` | Write HTML plots to `result_folder` (needs plotly) |
| `result_folder` | `~/printer_data/config/eddy_seek_results` | Output directory for debug artefacts |
| `debug` | `False` | Verbose console; pass `VERBOSE=1` on any command for one-off verbosity |
<!-- END:seek-config-main -->

### `[eddy_seek]` - `strategy: sweep_centroid` options

<!-- BEGIN:seek-config-sweep -->
| Option | Default | Description |
| ------ | ------- | ----------- |
| `sweep_coarse_speed` | `20` | Coarse sweep feedrate (mm/s) |
| `sweep_fine_speed` | `10` | Fine sweep feedrate (mm/s) |
| `sweep_overscan` | `1` | Extra travel beyond jog range (mm) |
| `sweep_cross_offset` | `0.3` | Stagger between parallel sweeps (mm) |
| `fine_shrink` | `0.6` | Fine pass range multiplier (x max_jog) |
| `min_sweep_samples` | `20` | Minimum profile points before centroid fit |
| `coarse_phases` | `2` | Coarse search passes before fine passes |
| `coarse_cross_passes` | `3` | Staggered sweep lines per coarse pass (fine uses 1) |
| `sweep_arc_resolution` | `0.1` | Max chord length per connector arc between sweeps (mm) |
<!-- END:seek-config-sweep -->

### `[eddy_seek]` - general notes

**max_jog** should be ≥ 2x your worst-case expected misalignment (per axis). Searches are unlikely to converge fully if the nozzle starts too far from the true centre.

**Speed units:** All speed values are in mm/s in `printer.cfg` and `EDDY_SEEK_SET`.

**Speed overrides:** Any move where samples are taken will dynamically adjust the speed to keep the sampling density at an acceptable level.

**Travel limits:** `sensor_x ± max_jog_x` and `sensor_y ± max_jog_y` must be within machine limits.

### Per-tool config sections

After alignment, offsets are saved under `{tool_prefix}{n}` (default `es_T1`, `es_T2`, …). Tool numbers are **0-based**.

Tool 0 does not get a section as it is the reference tool.

```ini
[es_T1]
offset_x: 0.000000 ; ❌
offset_y: 0.000000 ; ❌
manual_adjust_x: 0.000000 ; ✅ editable
manual_adjust_y: 0.000000 ; ✅ editable
is_calibrated: True ; ❌
```

Run `SAVE_CONFIG` to persist. `manual_adjust_*` values are **added** to the calibrated offset.

---

## Verify the sensor stream

```
EDDY_SEEK_QUERY
```

Expected output (numbers will vary):

```
Sensor 12.3 MHz (capture: 12.1 MHz, 42 samples, sample_rate: 400 Hz)
```

If `total` stays at `0`: check I2C wiring, `i2c_mcu` / `i2c_bus`, and `klippy.log` for `eddy_seek: initialised`.

---

## Alignment workflow

### Single-nozzle XY seek (`EDDY_SEEK_START`)

Finds the sensor centre from current XY position - for debugging or repeatability checks e.g. to check your `sensor_x` and `sensor_y` positions.

### Toolchanger alignment (`EDDY_SEEK_TOOL` / `EDDY_SEEK_TOOLS`)

**Tool 0** establishes the reference centre. **Other tools** seek at that centre; the offset is Tool n → Tool 0.

- Set `sensor_x`/`sensor_y` near the coil; tool 0 jogs there automatically.
- The seek refines within `max_jog` - You will get a warning and suggested change if your sensor position is borderline wrong.
- **Z is not changed** - park at `sensor_z` before running alignment commands.If you have `sensor_z` set, EddySeek errors if machine Z is outside the range of `[sensor_z, sensor_z + 0.25]` mm.

**One tool:** load each tool, then `EDDY_SEEK_TOOL TOOL=n`. Run `SAVE_CONFIG` after each.

**All tools:** `EDDY_SEEK_TOOLS` (runs load macros `T0`…`Tn`). Run `SAVE_CONFIG` once at the end.

`REPEATS=n` (default 3) runs each tool's seek `n` times at the same start position and saves the **mean** offset. With `n >= 2`, repeatability stats (σ, max scatter) match `EDDY_SEEK_ACCURACY`.

---

## G-code commands

<!-- BEGIN:gcode-commands -->
| Command | Description |
| ------- | ----------- |
| `EDDY_SEEK_QUERY` | Print frequency statistics |
| `EDDY_SEEK_RESET` | Manually clear capture buffer (not usually needed) |
| `EDDY_SEEK_SET [<key>=<value> …]` | Override config until `FIRMWARE_RESTART`. Bare command prints current values (e.g. `STRATEGY=<enum>`, `TOLERANCE=<float>`). |
| `EDDY_SEEK_START [STRATEGY=<enum>]` | XY search from current position |
| `EDDY_SEEK_ACCURACY [REPEATS=<int> MOCK=<0\|1>]` | Run full seeks (default 3, min 2, max 50) and report σ / max scatter. `MOCK=1` applies a small random start offset each repeat. |
| `EDDY_SEEK_TOOL TOOL=<int> [REPEATS=<int> LOAD=<0\|1> STRATEGY=<enum>]` | Align one tool. Caller loads the tool unless `LOAD=1`. `REPEATS` seeks are averaged per tool (default 3).<br><br>⚠️Your load macro must put the toolhead in a position where it is safe to move X to tool 0's center, and then Y to tool 0's center.⚠️ |
| `EDDY_SEEK_TOOLS [TOOLS=<int> REPEATS=<int>]` | Align tools 0…n−1 with averaged seeks (default: all tools, 3 repeats each). |
| `EDDY_SEEK_APPLY_OFFSET [TOOL=<int>]` | DIY only: apply saved XY via `SET_GCODE_OFFSET`. Errors on INDX (`CHANGE_TOOL` owns apply). |
<!-- END:gcode-commands -->

---

## Search strategies

### Sweep centroid (`strategy: sweep_centroid`) - default

Continuous axis sweeps (like Klipper's bed mesh `rapid_scan` method). Coarse bidirectional sweeps, then finer passes; samples merged into a frequency-weighted 2D centroid. Best compromise between speed and reliability.

### Centroid (`strategy: centroid`)

3x3 grid around the current best point with `dwell_time` at each probe. Grid spacing is `max_jog_x/y / 2`, halving each pass. Very slow - backup strategy when sweep centroid sample rate is too low.

### Debug scan (`strategy: debug_scan`)

Diagnostic grid only - [see troubleshooting](#debug-scan-strategy-debug_scan). Do not use for alignment.

---

## Debug plots and session traces

Requires plotly on the Klipper host:

```bash
~/klippy-env/bin/pip3 install plotly
```

With `save_plots: True`, HTML plots land under `{result_folder}/YYYY-MM-DD_HH-MM-SS_{run_label}/` (for example `2026-07-02_14-30-26_start/`). Download and open in a browser (Mainsail shows source, not the plot).

---

## Moonraker / host API

`eddy_seek` is queryable via `printer.objects.query` / `subscribe`. Key fields: `last_freq`, `smooth_mean`, `capture_mean`, `capture_count`, `total_samples`, `sample_rate_hz`, `toolchanger_type`, and `tools` (per-tool offsets and calibration state).

---

## Troubleshooting

| Symptom                                                                       | Things to check                                                                                                |
| ----------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `total` stays 0 on `EDDY_SEEK_QUERY`                                          | I2C wiring, `i2c_mcu` / `i2c_bus`, `klippy.log`                                                                |
| `no samples at offset` during seek                                            | Increase `dwell_time`; check coil height and sensor stream                                                     |
| Search does not converge                                                      | `max_passes`, `max_jog_x/y`, `search_for`, try another `strategy`                                              |
| `pass corrections diverging`                                                  | Nozzle too far from centre - fix `sensor_x/y`, `max_jog`, or Z height                                          |
| Sweep centroid: too few samples                                               | Lower `sweep_fine_speed`; check LDC1612 stream; Run `EDDY_SEEK_QUERY` and check your sample rate is ~360-400Hz |
| `tool 0 must be aligned before other tools`                                   | Klipper restart cleared the reference; run `EDDY_SEEK_TOOL TOOL=0` or `EDDY_SEEK_TOOLS`                        |
| Offsets not in `printer.cfg` (DIY)                                            | Run `SAVE_CONFIG` after alignment                                                                              |
| INDX: config error on `tool_count` / `load_tool_macro_prefix` / `tool_prefix` | Remove those keys; INDX owns tool count and load macros                                                        |
| Startup suggests `toolchanger_type: indx`                                     | Set `toolchanger_type: indx` in `[eddy_seek]` if you use Bondtech INDX macros                                  |
| `EDDY_SEEK_APPLY_OFFSET` on INDX                                              | Not supported - `CHANGE_TOOL` applies XY from save_variables                                                   |

### Debug scan (`strategy: debug_scan`)

Diagnostic only - not for alignment.

```gcode
EDDY_SEEK_SET SAVE_PLOTS=True STRATEGY=debug_scan
EDDY_SEEK_START
```

Runs a grid over the full jog area. Useful to confirm the sensor sees a signal within your configured range.

---

## Example plots

| Method             | Example Plot                                  |
| ------------------ | --------------------------------------------- |
| **Sweep centroid** | ![Sweep centroid](./plots/sweep_centroid.png) |
| **Debug scan**     | ![Debug scan](./plots/debug_scan.png)         |

## License

EddySeek is licensed under the [GNU General Public License v3.0](../LICENSE).
