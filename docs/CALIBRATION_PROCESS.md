# EddySeek Calibration Process

This document describes how EddySeek calibrates nozzle XY positions using the
LDC1612 eddy-current sensor. It covers the user-facing workflow, the multi-tool
sequence, and the internal XY search loop.

For install, configuration, and G-code reference, see the [User Guide](USER_GUIDE.md).

---

## Overview

Calibration finds where each nozzle sits relative to a **reference point** on the
sensor coil. The coil frequency changes as the nozzle moves in XY; EddySeek jogs
the toolhead, samples frequency at each point, and converges on the peak (or
valley) that marks the sensor centre.

| Mode                 | Command                 | Result                                               |
| -------------------- | ----------------------- | ---------------------------------------------------- |
| Single-position seek | `EDDY_SEEK_START`       | Offset from current XY to sensor centre              |
| One tool             | `EDDY_SEEK_TOOL TOOL=n` | Per-tool offset staged in config autosave            |
| All tools            | `EDDY_SEEK_TOOLS`       | Tool 0 defines centre; tools 1…N measured against it |

After any tool alignment command succeeds, run `SAVE_CONFIG` to write offsets to
`printer.cfg`.

---

## End-to-end toolchanger workflow

```mermaid
flowchart TD
    A[Install eddy_seek + configure I2C] --> B[FIRMWARE_RESTART]
    B --> C[EDDY_SEEK_QUERY - verify sensor stream]
    C --> D[Park tool 0 above sensor at probe height]
    D --> E{Alignment mode}
    E -->|All tools| F[EDDY_SEEK_TOOLS]
    E -->|One at a time| G[EDDY_SEEK_TOOL TOOL=0]
    G --> H[EDDY_SEEK_TOOL TOOL=n — load macro + move to centre]
    H --> MORE{More tools?}
    MORE -->|yes| H
    MORE -->|no| J[Offsets staged in T0, T1, … sections]
    F --> J
    J --> K[SAVE_CONFIG]
    K --> L[Wire offsets into toolchanger / motion system]
```

**Tool 0** is special: it establishes the absolute sensor-centre XY in machine
coordinates. **Subsequent tools** are loaded, moved to that centre, then seeked;
the resulting offset is the XY difference from tool 0.

---

## Multi-tool alignment sequence

`EDDY_SEEK_TOOLS` (and repeated `EDDY_SEEK_TOOL` calls) follow this logic:

```mermaid
flowchart TD
    START([EDDY_SEEK_TOOLS]) --> SAVE[Save G-code state]
    SAVE --> LOOP{For each tool 0…N-1}

    LOOP -->|tool 0| T0_MOVE[Move to sensor_x/sensor_y]
    T0_MOVE --> T0_START[Record start XY]
    T0_START --> T0_SEEK[Run XY seek from sensor position]
    T0_SEEK --> T0_OK{Seek OK?}
    T0_OK -->|no| FAIL([Report error, restore state])
    T0_OK -->|yes| T0_CENTER["tool0_center = start + offset<br/>offset_x/y = 0"]
    T0_CENTER --> STAGE0[Stage T0 in autosave]

    LOOP -->|tool 1…N-1| LOAD[Run load macro Tn]
    LOAD --> MOVE[Move to tool0_center XY]
    MOVE --> TN_SEEK[Run XY seek from centre]
    TN_SEEK --> TN_OK{Seek OK?}
    TN_OK -->|no| FAIL
    TN_OK -->|yes| TN_OFF["offset_x/y = seek result<br/>(difference from tool 0)"]
    TN_OFF --> STAGEN[Stage Tn in autosave]

    STAGE0 --> LOOP
    STAGEN --> LOOP
    LOOP -->|done| DONE([Report success - run SAVE_CONFIG])
    DONE --> RESTORE[Restore G-code state]
```

---

## XY seek session (`EDDY_SEEK_START`)

Every alignment measurement runs through `SeekSession`. The toolhead starts at
the user's parked position; the session jogs within `max_jog_x` / `max_jog_y`,
reports the final offset, then **returns to the starting XY**.

```mermaid
sequenceDiagram
    participant User
    participant GCode as G-code / SeekSession
    participant Strategy as Search strategy
    participant Toolhead
    participant Sensor as LDC1612 stream

    User->>GCode: EDDY_SEEK_START
    GCode->>GCode: Save G-code state
    GCode->>Strategy: search()

    loop Up to max_passes
        Strategy->>Toolhead: measure_at(x, y) - multiple probes
        Toolhead->>Sensor: dwell at each point
        Sensor-->>GCode: frequency samples → capture buffer
        GCode-->>Strategy: capture mean per point
        Strategy->>Strategy: Update best X/Y offset
        alt Both axes moved < tolerance
            Strategy-->>GCode: Converged
        end
    end

    GCode->>Toolhead: Move to best offset
    GCode->>User: Report X/Y offset from start
    GCode->>GCode: Restore G-code state (return to start XY)
```

---

## Search pass loop

Each pass refines the best-known nozzle offset. The configured **strategy**
decides how probe points are chosen within the jog radius.

```mermaid
flowchart TD
    PASS([Start pass N]) --> STEP[Strategy step: probe, compute new X/Y]
    STEP --> MOVE["Compare movement vs previous best"]
    MOVE --> CHECK{both axes < tolerance?}
    CHECK -->|yes| CONV([Converged - use best offset])
    CHECK -->|no| MORE{N < max_passes?}
    MORE -->|yes| PASS
    MORE -->|no| MAX([Use best result anyway])
```

### Ternary strategy (`strategy: ternary`)

Each pass runs a 1-D ternary search on **X**, then **Y**, within the jog radius.
Up to `max_iter` subdivisions per axis.

```mermaid
flowchart LR
    subgraph pass["One pass"]
        X["Ternary search X<br/>(Y fixed at best_y)"] --> Y["Ternary search Y<br/>(X fixed at new best_x)"]
    end
    pass --> OUT["New (best_x, best_y)"]
```

At each ternary subdivision, two probe points divide the interval; the side with
the better frequency (per `search_for: max|min`) is kept.

### Centroid strategy (`strategy: centroid`)

Each pass probes a **3×3 grid** around the current best point. Grid spacing
halves every pass (`grid_step × 0.5^(pass−1)`). Sampled frequencies are
weighted toward the target extreme and the toolhead moves to the weighted
centroid.

```mermaid
flowchart TD
  GRID["Probe 9 points on grid"] --> WEIGHT["Weight each sample<br/>(closer to max/min = higher weight)"]
  WEIGHT --> CENT["Move to weighted centroid"]
  CENT --> CLAMP["Clamp within max_jog_x/y"]
```

---

## Single probe cycle (`measure_at`)

Every probe point - whether from ternary or centroid - follows the same
measurement steps:

```mermaid
flowchart TD
    A[Jog to offset from session start] --> B[reset_capture - start buffering samples]
    B --> C[dwell_time wait]
    C --> D[get_capture_mean]
    D --> E{≥ 3 samples?}
    E -->|no| ERR([Error: no samples at offset])
    E -->|yes| F[Return mean frequency to strategy]
```

The LDC1612 driver pushes frequency batches continuously; `reset_capture` marks
the window used for that probe point.

---

## What gets saved

After alignment, staged config sections look like:

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

| Tool  | `offset_x` / `offset_y` meaning                             |
| ----- | ----------------------------------------------------------- |
| T0    | Always `0, 0` - defines the reference centre                |
| T1…Tn | XY shift needed so this nozzle matches tool 0 on the sensor |

`SAVE_CONFIG` persists these values. Your toolchanger macros or motion system
apply them when switching tools.

---

## Related commands

| Command              | Role in calibration                                       |
| -------------------- | --------------------------------------------------------- |
| `EDDY_SEEK_QUERY`    | Confirm live sensor data before calibrating               |
| `EDDY_SEEK_RESET`    | Clear capture buffer                                      |
| `EDDY_SEEK_SET`      | Tune tolerance, strategy, dwell, etc. without editing cfg |
| `EDDY_SEEK_ACCURACY` | Repeat `EDDY_SEEK_START` and report repeatability stats   |
