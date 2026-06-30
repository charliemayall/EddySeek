# EddySeek User Guide

EddySeek is a Klipper extra for **nozzle alignment on toolchanger printers** using an
LDC1612 eddy-current sensor. It reads live coil frequency, runs XY search routines,
and can measure per-tool offsets relative to a reference nozzle.

---

## What you need

- Klipper or Kalico
- An LDC1612 eddy-current probe (dedicated to nozzle alignment - not your bed-mesh probe)
- A toolchanger or multi-nozzle setup where each tool can be parked above the sensor
- A G-code macro (or command) that loads each tool, e.g. `T0`, `T1`, …

---

## Install

Symlink the modules into Klipper's extras directory (default). Symlinks let
Moonraker update-manager apply `git pull` updates without re-running install.
The whole `_eddy_seek` package is linked as one directory, so new modules show
up after pull without another install run.

```bash
./install.sh
```

You can specify Klipper's extras path if it is not the default.

```bash
./install.sh ~/my_non_standard_dir/klipper/klippy/extras
```

**Moonraker update-manager** — add to `moonraker.conf`, run `./install.sh` once,
then updates pull the repo and restart Klipper:

```ini
[update_manager eddy_seek]
type: git_repo
path: ~/EddySeek
origin: https://github.com/charliemayall/EddySeek.git
primary_branch: main
managed_services: klipper
is_system_service: False
```

Add configuration to `printer.cfg` (see below), then restart Klipper:

```
FIRMWARE_RESTART
```

---

## Stop

There are a few things you need to know going forwards:

### Watch your printer

The first time you run the alignment commands, be ready to stop your printer.

### Alignments start from 2 places:

**Tool 0** - from the configured `sensor_x`,`sensor_y`

**Other tools** - from tool 0's centre

### Tool 0 must be aligned after every restart

You must run Tool 0 after every restart, if you wish to align other tools.

You do not need to calibrate Tool 0 to use previously calibrated tools.

### Z position

Sensors such as the BigTreeTech Eddy recommend a specific spacing between the sensor and the build plate.

You should position your sensor so the nozzle will be this distance above the sensor when it is being aligned.

The calibration process will not change your Z position, you should be at the desired Z position before you run the alignment commands.

---

## Hardware and sensor setup

The LDC1612 is configured **inside** `[eddy_seek]`. Use a dedicated probe for
nozzle alignment - not your bed-mesh probe.

| Option        | Description                               |
| ------------- | ----------------------------------------- |
| `sensor_type` | Must be `ldc1612`                         |
| `i2c_address` | I2C address (default `42` / `0x2a`)       |
| `i2c_mcu`     | MCU the sensor is wired to, e.g. `mcu`    |
| `i2c_bus`     | Hardware I2C bus on that MCU, e.g. `i2c1` |

Example:

```ini
[eddy_seek]
sensor_type: ldc1612
i2c_address: 42
i2c_mcu: mcu
i2c_bus: i2c1
```

Optional LDC1612 tuning keys (same as Klipper's `[ldc1612]` section) can also
live in `[eddy_seek]`, e.g. `frequency`, `max_sensor_hz`, `reg_drive_current`.

---

## Configuration reference

### `[eddy_seek]` section

| Option                   | Default         | Description                                                         |
| ------------------------ | --------------- | ------------------------------------------------------------------- |
| `sensor_type`            | _(required)_    | `ldc1612`                                                           |
| `i2c_address`            | `42`            | LDC1612 I2C address (`0x2a`)                                        |
| `i2c_mcu`                | _(required)_    | MCU name, e.g. `mcu`                                                |
| `i2c_bus`                | _(required)_    | I2C bus on that MCU, e.g. `i2c1`                                    |
| `tool_count`             | `1`             | Number of tools on the changer                                      |
| `tool_prefix`            | `T`             | Prefix for saved offset sections (`T0`, `T1`, …)                    |
| `load_tool_macro_prefix` | `T`             | Prefix for the G-code that loads a tool (`T` → macro `T0`, `T1`, …) |
| `sensor_x`               | _(required)_    | Machine X of the sensor coil; tool 0 moves here before seeking      |
| `sensor_y`               | _(required)_    | Machine Y of the sensor coil; tool 0 moves here before seeking      |
| `window_size`            | `20`            | Rolling mean window for live frequency stats                        |
| `max_jog_x`              | `5.0`           | Max X search radius from start (mm)                                 |
| `max_jog_y`              | `5.0`           | Max Y search radius from start (mm)                                 |
| `tolerance`              | `0.1`           | Stop a pass when X and Y movement are both below this (mm)          |
| `dwell_time`             | `0.5`           | Seconds to wait at each probe point for samples                     |
| `jog_speed`              | `600`           | Feedrate for search jogs (mm/min)                                   |
| `search_for`             | `max`           | `max` or `min` - which frequency extreme marks the nozzle centre    |
| `strategy`               | `ternary`       | `ternary` or `centroid`                                             |
| `grid_step_x`            | `max_jog_x / 2` | Centroid grid spacing in X (mm)                                     |
| `grid_step_y`            | `max_jog_y / 2` | Centroid grid spacing in Y (mm)                                     |
| `max_iter`               | `10`            | Ternary iterations per axis per pass                                |
| `max_passes`             | `6`             | Alternating X/Y search passes before giving up                      |
| `save_session_trace`     | `False`         | Write probe data to `/tmp/seek_trace.json` after each seek (debug)  |

Example for a four-tool changer:

```ini
[eddy_seek]
sensor_type: ldc1612
i2c_address: 42
i2c_mcu: mcu
i2c_bus: i2c1

tool_count: 4
tool_prefix: T
load_tool_macro_prefix: T

sensor_x: 20.0
sensor_y: 20.0
window_size: 20
max_jog_x: 5.0
max_jog_y: 5.0
tolerance: 0.1
dwell_time: 0.5
jog_speed: 600
search_for: max
strategy: ternary
grid_step_x: 2.5
grid_step_y: 2.5
max_iter: 10
max_passes: 6
save_session_trace: True
```

### Per-tool offset sections

After alignment, offsets are staged in the config autosave under sections named
`{tool_prefix}{n}` (default `T0`, `T1`, …). Tool numbers are **0-based**.

```ini
[T0]
offset_x: 0.000000
offset_y: 0.000000
is_calibrated: True

[T1]
offset_x: 1.234000
offset_y: -0.456000
is_calibrated: True
```

Run `SAVE_CONFIG` in the console to persist staged values to `printer.cfg`.

---

## Verify the sensor stream

Run in the G-code console:

```
EDDY_SEEK_QUERY
```

Expected output (values will vary):

```
EDDY_SEEK: last=12345678.0 Hz  window_mean=12345678.0 Hz
                   capture_mean=0.0 Hz  capture_count=0  total=42
```

If `total` stays at `0`, check the following:

- `eddy_seek.py` and `_eddy_seek/` are installed (or symlinked)
- `sensor_type`, `i2c_mcu`, and `i2c_bus` are set correctly in `[eddy_seek]`
- The probe is wired and the driver initialized
- Check `klippy.log` for `eddy_seek: initialised` and subscription messages

---

## Alignment workflow

### Single-nozzle XY seek (`EDDY_SEEK_START`)

Use this to find the sensor centre at the current XY position - for calibration,
repeatability checks, or manual offset measurement.

1. Home and move the **reference nozzle** above the eddy sensor at probing height.
2. Optionally clear the capture buffer: `EDDY_SEEK_RESET`
3. Run: `EDDY_SEEK_START`
4. The toolhead jogs and samples until converged or `max_passes` is reached.
5. Read the reported offset from the start position, e.g.:

```
 EDDY_SEEK: done - nozzle offset from start: X=+1.2340 mm  Y=-0.4560 mm  (passes=2)
```

The toolhead returns to the starting XY when the command finishes.

### Toolchanger alignment (`EDDY_SEEK_TOOL` / `EDDY_SEEK_TOOLS`)

**Tool 0** establishes the reference centre on the sensor. **Subsequent tools** are
loaded, moved to that centre, then seeked; the resulting offset is the XY difference
from tool 0.

> **Tool 0 must be aligned before other tools.**

> **Auto-positioning:** set `sensor_x`/`sensor_y` to the sensor coil's
> machine XY position. Tool 0 jogs there automatically before seeking.
> The seek refines within `max_jog`, so the coordinates only need to be within a few mm of
> the true centre. **Z is not changed** - park at probe height (e.g. in your
> tool-load macro) first.

#### One tool at a time

```
; Park tool 0 above the sensor, then:
EDDY_SEEK_TOOL TOOL=0

; Tool 1+ — EddySeek runs the load macro and moves to tool 0's centre:
EDDY_SEEK_TOOL TOOL=1
```

After each successful run, run `SAVE_CONFIG` to persist offsets.

> **Session note:** Tool 0's sensor-centre XY is kept in memory for
> `EDDY_SEEK_TOOL` only until Klipper restarts. Align tool 0 again before
> other tools if you `FIRMWARE_RESTART` between runs. `EDDY_SEEK_TOOLS` runs
> the full sequence in one command and avoids that gap.

#### All tools in sequence

Park **tool 0** above the sensor, then:

```
EDDY_SEEK_TOOLS
; or override count:
EDDY_SEEK_TOOLS TOOLS=4
```

EddySeek loads each tool via `{load_tool_macro_prefix}{n}` (default `T0`, `T1`, …),
aligns it, and stages offsets. Finish with `SAVE_CONFIG`.

**Typical first-time sequence**

1. Install and configure `[eddy_seek]` with I2C settings.
2. `FIRMWARE_RESTART`
3. `EDDY_SEEK_QUERY` - confirm samples increment.
4. Load tool 0, jog above sensor at probe height.
5. `EDDY_SEEK_TOOLS` (or `EDDY_SEEK_TOOL TOOL=0` then repeat for each tool).
6. `SAVE_CONFIG`
7. Wire saved `T{n}` offsets into your toolchanger / motion system as needed.

---

## G-code commands

| Command                         | Description                                           |
| ------------------------------- | ----------------------------------------------------- |
| `EDDY_SEEK_QUERY`               | Print current frequency statistics                    |
| `EDDY_SEEK_RESET`               | Clear capture buffer before a measurement             |
| `EDDY_SEEK_SET`                 | Override seek settings until restart (see below)      |
| `EDDY_SEEK_START`               | Run XY search from current position                   |
| `EDDY_SEEK_ACCURACY`            | Repeat alignment and report repeatability             |
| `EDDY_SEEK_TOOL TOOL=n`         | Align one tool (0-based)                              |
| `EDDY_SEEK_TOOLS`               | Align all tools against tool 0                        |
| `EDDY_SEEK_APPLY_OFFSET TOOL=n` | Apply saved XY offset for a tool via SET_GCODE_OFFSET |

### `EDDY_SEEK_SET`

Temporarily change search parameters without editing `printer.cfg`. Parameters
match the `[eddy_seek]` seek keys (G-code names are uppercase):

`WINDOW_SIZE`, `MAX_JOG_X`, `MAX_JOG_Y`, `TOLERANCE`, `DWELL_TIME`, `JOG_SPEED`,
`SEARCH_FOR`, `STRATEGY`, `GRID_STEP_X`, `GRID_STEP_Y`, `MAX_ITER`, `MAX_PASSES`

```
EDDY_SEEK_SET STRATEGY=centroid
EDDY_SEEK_SET TOLERANCE=0.05 MAX_PASSES=8
EDDY_SEEK_SET
```

Run `EDDY_SEEK_SET` without parameters to print current values.

Overrides last until Klipper restarts.

### `EDDY_SEEK_ACCURACY`

```
EDDY_SEEK_ACCURACY REPEATS=5
```

Runs full `EDDY_SEEK_START` alignment `REPEATS` times (default 3, min 2, max 50),
returns to the start XY between runs, then prints mean, standard deviation, radial
scatter, and max pairwise distance. Useful for tuning `dwell_time`, `tolerance`, and
`strategy`.

---

## Search strategies

### Ternary (`strategy: ternary`)

Each pass runs a 1-D ternary search on X, then Y, within `max_jog_x` / `max_jog_y`.
Good default when the frequency peak is smooth and single-valued.

### Centroid (`strategy: centroid`)

Each pass probes a 3×3 grid around the current best point, weights samples by how
close each frequency is to the target extreme (`search_for`), and moves to the
weighted centroid. Grid step halves each pass. Useful when the response is broader
or slightly asymmetric.

Set `search_for` to `max` if the nozzle centre gives the **highest** frequency, or
`min` if it gives the **lowest** (depends on coil geometry and target material).

---

## Moonraker / host API

The `eddy_seek` printer object is available via `printer.objects.query` and
`printer.objects.subscribe`.

| Field           | Description                                             |
| --------------- | ------------------------------------------------------- |
| `last_freq`     | Most recent sample (Hz)                                 |
| `window_mean`   | Rolling mean of last `window_size` samples (Hz)         |
| `capture_mean`  | Mean since last `EDDY_SEEK_RESET` (Hz)                  |
| `capture_count` | Samples in current capture session                      |
| `total_samples` | Total samples since Klipper started                     |
| `tools`         | Map of `T{n}` → `offset_x`, `offset_y`, `is_calibrated` |

---

## Troubleshooting

| Symptom                                     | Things to check                                                                      |
| ------------------------------------------- | ------------------------------------------------------------------------------------ |
| `total` stays 0 on `EDDY_SEEK_QUERY`        | I2C wiring, `i2c_mcu` / `i2c_bus`, `klippy.log` init errors                          |
| `no samples at offset` during seek          | Increase `dwell_time`; verify sensor stream; check coil height                       |
| Search does not converge                    | Increase `max_passes` or `max_jog_x/y`; try `centroid`; check `search_for` direction |
| `tool 0 must be aligned before other tools` | Run `EDDY_SEEK_TOOL TOOL=0` or start `EDDY_SEEK_TOOLS` from tool 0                   |
| Tool load fails                             | `load_tool_macro_prefix` must match your macros (`T0`, `LOAD_TOOL_0`, etc.)          |
| Offsets not in `printer.cfg`                | Run `SAVE_CONFIG` after alignment commands succeed                                   |

---

## License

EddySeek is licensed under the [GNU General Public License v3.0](../LICENSE).
