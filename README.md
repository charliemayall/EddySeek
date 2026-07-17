# EddySeek

[![version](https://img.shields.io/badge/dynamic/toml?url=https://raw.githubusercontent.com/charliemayall/EddySeek/main/pyproject.toml&query=%24.project.version&label=version)](https://github.com/charliemayall/EddySeek)
[![license](https://img.shields.io/badge/dynamic/toml?url=https://raw.githubusercontent.com/charliemayall/EddySeek/main/pyproject.toml&query=%24.project.license&label=license)](LICENSE)

**Nozzle alignment for Klipper toolchangers using an LDC1612 eddy-current sensor.**

EddySeek reads an LDC1612 coil, runs XY search routines, and measures per-tool XY offsets relative to a reference nozzle.

> 🙏 **Help wanted:** Do you own a toolchanger and / or cartographer / beacon? Please try EddySeek and help me expand the range of setups it has been tested on.

> ⚠️ **Early release.** Not yet
> proven across many machines. Validate alignment results on your own hardware
> before relying on them, and keep an eye on the toolhead during the first runs.

## Near-drop-in support

- Bondtech INDX (select during install prompt, or via `toolchanger_type` in `[eddy_seek]` config section)

- Any other toolchanger will still work, it just takes a bit more integration on your side.

## Getting Started

```bash
cd ~
git clone https://github.com/charliemayall/EddySeek.git
cd EddySeek
./install.sh

```

You will be prompted with a few questions to help you get set up quickly.

Then, see the [User Guide](docs/USER_GUIDE.md) for configuration and usage.

Optional: Add to `moonraker.conf` for update-manager support

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

## Full Documentation

**[User Guide](docs/USER_GUIDE.md)** - install, configuration, G-code reference,
toolchanger alignment workflow, strategies, Moonraker fields, and troubleshooting.

## Requirements

- Klipper / Kalico
- LDC1612 eddy-current sensor (dedicated probe for nozzle alignment)
- Tool-load G-code macros for each tool (e.g. `T0`, `T1`, …) - EddySeek does not load tools for you

## Development

```bash
uv sync --group dev
uv run ruff check .
uv run pytest
```

> For workflow diagrams and the internal seek loop, see [Calibration Process](docs/CALIBRATION_PROCESS.md).

## License

[GNU GPLv3](LICENSE)
