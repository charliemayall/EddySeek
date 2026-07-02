# EddySeek

**Nozzle alignment for Klipper toolchangers using an LDC1612 eddy-current sensor.**

EddySeek reads an LDC1612 coil, runs XY search routines, and measures per-tool XY offsets relative to a reference nozzle.

> 🙏 **Help wanted:** Do you own a toolchanger and / or cartographer / beacon? Please try EddySeek and help me expand the range of setups it has been tested on.

> ⚠️ **Early release.** Not yet
> proven across many machines. Validate alignment results on your own hardware
> before relying on them, and keep an eye on the toolhead during the first runs.

## Demo

![Centroid search demo](docs/media/demo_centroid.webp)

Full quality: [`docs/media/demo_centroid.mp4`](docs/media/demo_centroid.mp4)

> **Note:** The video shows a stationary screw with a sensor being aligned over it for demonstration.
> On a toolchanger, the **nozzle** moves and the **sensor stays fixed** on the bed or frame.

## Quick start

```bash
cd ~
git clone https://github.com/charliemayall/EddySeek.git
cd EddySeek
./install.sh
```

Add to `moonraker.conf` for update-manager support:

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

Add `[eddy_seek]` to `printer.cfg` (I2C settings plus `sensor_x` / `sensor_y` — see the [User Guide](docs/USER_GUIDE.md)), `FIRMWARE_RESTART`, then:

```gcode
EDDY_SEEK_QUERY
EDDY_SEEK_START
```

For toolchanger workflows, see the full guide. Per-tool sections (`T0`, `T1`, …) support optional `manual_adjust_x` / `manual_adjust_y` tweaks (mm) added on top of measured offsets when applying alignment.

## Documentation

**[User Guide](docs/USER_GUIDE.md)** - install, configuration, G-code reference,
toolchanger alignment workflow, strategies, Moonraker fields, and troubleshooting.

## Requirements

- Klipper / Kalico
- LDC1612 eddy-current sensor (dedicated probe for nozzle alignment)
- Tool-load G-code macros for each tool (e.g. `T0`, `T1`, …)

## Development

```bash
uv sync --group dev
uv run ruff check .
uv run pytest
```

> For an overview of the states and processes covered by this codebase, see [Calibration Process](docs/CALIBRATION_PROCESS.md).

## License

[GNU GPLv3](LICENSE)
