# EddySeek User Guide

EddySeek is a Klipper extra for **nozzle alignment on toolchanger printers** using an
LDC1612 eddy-current sensor. It reads live coil frequency, runs XY search routines,
and measures per-tool offsets relative to a reference nozzle.

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
```

Optional LDC1612 tuning keys (`frequency`, `max_sensor_hz`, `reg_drive_current`, …) can live here too.

## Minimal calibration workflow

1. Add `[eddy_seek]` with I2C settings and `sensor_x` / `sensor_y` (doesn't have to be absolutely accurate).
2. `FIRMWARE_RESTART`
3. `EDDY_SEEK_QUERY` - confirm samples increment.
4. Load tool 0, park at probe height above the sensor (EddySeek does not move Z).
5. `EDDY_SEEK_TOOLS` (or `EDDY_SEEK_TOOL TOOL=n` per tool).
6. `SAVE_CONFIG`
7. `EDDY_SEEK_APPLY_OFFSET TOOL=n` in toolchanger macros or your slicer.

After a Klipper restart, run tool 0 again before aligning other tools - or use `EDDY_SEEK_TOOLS`, which runs tool 0 first.

---

## Configuration reference

### `[eddy_seek]` - main options

| Option                    | Default                                   | Description                                                                            |
| ------------------------- | ----------------------------------------- | -------------------------------------------------------------------------------------- |
| `sensor_type`             | _(required)_                              | `ldc1612`                                                                              |
| `i2c_address`             | `42`                                      | LDC1612 I2C address (`0x2a`)                                                           |
| `i2c_mcu`                 | _(required)_                              | MCU name, e.g. `mcu`                                                                   |
| `i2c_bus`                 | _(required)_                              | I2C bus, e.g. `i2c1`                                                                   |
| `tool_count`              | `1`                                       | Number of tools                                                                        |
| `tool_prefix`             | `T`                                       | Prefix for saved offset sections (`es_T1`, …)                                          |
| `load_tool_macro_prefix`  | `T`                                       | Prefix for load macros (`T0`, `T1`, …)                                                 |
| `sensor_x` / `sensor_y`   | _(required)_                              | Machine XY of sensor coil; tool 0 jogs here before seeking                             |
| `max_jog_x` / `max_jog_y` | `2.5`                                     | Max search radius from start (mm)                                                      |
| `tolerance`               | `0.05`                                    | Stop when both axes move less than this (mm)                                           |
| `dwell_time`              | `0.5`                                     | Seconds at each probe point (grid strategies only)                                     |
| `jog_speed`               | `10`                                      | Feedrate for search jogs (mm/s)                                                        |
| `search_for`              | `max`                                     | `max` or `min` - which frequency extreme marks the nozzle centre, `max` for most users |
| `strategy`                | `sweep_centroid`                          | `sweep_centroid`, `centroid`, `circle_harmonic`, or `debug_scan` (diag only)           |
| `max_passes`              | `6`                                       | Search passes before giving up                                                         |
| `save_session_trace`      | `False`                                   | Write probe JSON to `result_folder` (debug)                                            |
| `save_plots`              | `False`                                   | Write HTML plots to `result_folder` (needs plotly)                                     |
| `result_folder`           | `~/printer_data/config/eddy_seek_results` | Output directory for debug artefacts                                                   |
| `debug`                   | `False`                                   | Verbose console; pass `VERBOSE=1` on any command for one-off verbosity                 |

### `[eddy_seek]` - `strategy: sweep_centroid` options

| Option               | Default | Description                                |
| -------------------- | ------- | ------------------------------------------ |
| `sweep_coarse_speed` | `20`    | Coarse sweep feedrate (mm/s)               |
| `sweep_fine_speed`   | `10`    | Fine sweep feedrate (mm/s)                 |
| `sweep_overscan`     | `1.0`   | Extra travel beyond jog range (mm)         |
| `sweep_cross_offset` | `0.3`   | Stagger between parallel sweeps (mm)       |
| `sweep_cross_passes` | `3`     | Staggered sweep lines per axis             |
| `fine_shrink`        | `0.4`   | Fine pass range multiplier (× max_jog)     |
| `min_sweep_samples`  | `20`    | Minimum profile points before centroid fit |

### `[eddy_seek]` - `strategy: circle_harmonic` options

| Option                  | Default | Description                                      |
| ----------------------- | ------- | ------------------------------------------------ |
| `circle_radius_start`   | `2`     | First circle radius (mm)                         |
| `circle_radius_min`     | `0.5`   | Smallest circle radius (mm)                      |
| `circle_shrink`         | `0.4`   | Radius multiplier each pass                      |
| `circle_arc_resolution` | `0.1`   | Arc segment length along the circle (mm)         |
| `circle_speed`          | `10`    | Circle trace feedrate (mm/s)                     |
| `noise_k`               | `1`     | SNR threshold (amplitude vs noise) for model fit |
| `harmonic_step_gain`    | `0.15`  | Fraction of fitted offset applied each pass      |
| `harmonic_min_quality`  | `0.5`   | Minimum fit quality to accept a pass             |

> **Speed units:** All speed values are in mm/s in `printer.cfg` and `EDDY_SEEK_SET`.

> **Speed overrides:** Any move where samples are taken will dynamically adjust the speed to keep the sampling density at an acceptable level.

> **Travel limits:** `sensor_x ± max_jog_x` and `sensor_y ± max_jog_y` must be within machine limits.

Example for a four-tool changer:

```ini
[eddy_seek]
sensor_type: ldc1612
i2c_address: 42
i2c_mcu: mcu
i2c_bus: i2c1

tool_count: 4
tool_prefix: es_T
load_tool_macro_prefix: T

sensor_x: 20.0
sensor_y: 20.0
tolerance: 0.05
strategy: sweep_centroid
max_passes: 6
```

### Per-tool offset sections

After alignment, offsets are staged under `{tool_prefix}{n}` (default `es_T1`, `es_T2`, …). Tool numbers are **0-based**.

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
- **Z is not changed** - park at probe height first.

**One tool:** load each tool, then `EDDY_SEEK_TOOL TOOL=n`. Run `SAVE_CONFIG` after each.

**All tools:** load tool 0, then `EDDY_SEEK_TOOLS` (runs load macros for tools 1…N). Run `SAVE_CONFIG` once at the end.

---

## G-code commands

| Command                         | Description                                  |
| ------------------------------- | -------------------------------------------- |
| `EDDY_SEEK_QUERY`               | Print frequency statistics                   |
| `EDDY_SEEK_RESET`               | Clear capture buffer                         |
| `EDDY_SEEK_SET`                 | Override settings until restart              |
| `EDDY_SEEK_START`               | XY search from current position              |
| `EDDY_SEEK_ACCURACY`            | Repeat alignment and report repeatability    |
| `EDDY_SEEK_TOOL TOOL=n`         | Align one tool (caller loads the tool)       |
| `EDDY_SEEK_TOOLS`               | Align all tools                              |
| `EDDY_SEEK_TOOLS TOOLS=n`       | Align tools 0…n−1 only                       |
| `EDDY_SEEK_APPLY_OFFSET TOOL=n` | Apply saved XY offset via `SET_GCODE_OFFSET` |

`EDDY_SEEK_SET STRATEGY=centroid TOLERANCE=0.05` - overrides last until firmware restart. Run bare `EDDY_SEEK_SET` to print current values.

`EDDY_SEEK_ACCURACY REPEATS=5` - runs full seeks (default 3, min 2, max 50) and prints σ / max scatter. Use to compare `dwell_time`, `tolerance`, or `strategy`.

---

## Search strategies

### Sweep centroid (`strategy: sweep_centroid`) - default

Continuous axis sweeps (like bed mesh `rapid_scan`). Coarse bidirectional sweeps, then finer passes; samples merged into a frequency-weighted 2D centroid. Best compromise between speed and reliability.

### Centroid (`strategy: centroid`)

3×3 grid around the current best point with `dwell_time` at each probe. Grid spacing is `max_jog_x/y / 2`, halving each pass. Very slow — backup strategy when sweep centroid sample rate is too low.

### Circle harmonic (`strategy: circle_harmonic`)

Bootstrap axis sweeps, then traces shrinking circles and fits a harmonic model to refine the centre. Fastest strategy, and very accurate, but is **extremely intolerant of larger initial misalignment** - `sensor_x`/`sensor_y` and `max_jog` must put the nozzle close to the true centre, and the misalignment of the nozzle itself must be small.

### Debug scan (`strategy: debug_scan`)

Diagnostic grid only - [see troubleshooting](#debug-scan-strategy-debug_scan). Do not use for alignment.

---

## Debug plots and session traces

Requires plotly on the Klipper host:

```bash
~/klippy-env/bin/pip3 install plotly
```

With `save_plots: True`, HTML plots land under `{result_folder}/YYYY-MM-DD_HH-MM-SS_{run_label}_{run_id}/` (for example `2026-07-02_14-30-26_start_a1b2c3d4/`). Download and open in a browser (Mainsail shows source, not the plot).

---

## Moonraker / host API

`eddy_seek` is queryable via `printer.objects.query` / `subscribe`. Key fields: `last_freq`, `smooth_mean`, `capture_mean`, `capture_count`, `total_samples`, `sample_rate_hz`, and `tools` (per-tool offsets and calibration state).

---

## Troubleshooting

| Symptom                                     | Things to check                                                       |
| ------------------------------------------- | --------------------------------------------------------------------- |
| `total` stays 0 on `EDDY_SEEK_QUERY`        | I2C wiring, `i2c_mcu` / `i2c_bus`, `klippy.log`                       |
| `no samples at offset` during seek          | Increase `dwell_time`; check coil height and sensor stream            |
| Search does not converge                    | `max_passes`, `max_jog_x/y`, `search_for`, try another `strategy`     |
| `pass corrections diverging`                | Nozzle too far from centre - fix `sensor_x/y`, `max_jog`, or Z height |
| Sweep centroid: too few samples             | Lower `sweep_fine_speed`; check LDC1612 stream                        |
| `tool 0 must be aligned before other tools` | Run `EDDY_SEEK_TOOL TOOL=0` or start `EDDY_SEEK_TOOLS` from tool 0    |
| Offsets not in `printer.cfg`                | Run `SAVE_CONFIG` after alignment                                     |

### Debug scan (`strategy: debug_scan`)

Diagnostic only - not for alignment.

```gcode
EDDY_SEEK_SET SAVE_PLOTS=True STRATEGY=debug_scan
EDDY_SEEK_START
```

Runs a grid over the full jog area. Useful to confirm the sensor sees a signal within your configured range.

---

## Example plots

### Sweep centroid

![Sweep centroid example](./plots/sweep_centroid.png)

### Debug scan

![Debug scan example](./plots/debug_scan.png)

## License

EddySeek is licensed under the [GNU General Public License v3.0](../LICENSE).
