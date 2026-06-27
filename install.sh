#!/bin/sh
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# Symlink EddySeek into Klipper's extras directory (default).
# Symlinks keep Moonraker update-manager pulls visible to Klipper after FIRMWARE_RESTART.
# Set EDDY_SEEK_INSTALL=copy for a one-shot file copy instead.
set -e
DEST="${1:-$HOME/klipper/klippy/extras}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
MODE="${EDDY_SEEK_INSTALL:-symlink}"

mkdir -p "$DEST"

install_one() {
	src="$1"
	dest="$2"
	if [ "$MODE" = copy ]; then
		cp "$src" "$dest"
	else
		ln -sf "$src" "$dest"
	fi
}

install_one "$ROOT/src/eddy_seek.py" "$DEST/eddy_seek.py"
mkdir -p "$DEST/_eddy_seek"
for f in "$ROOT/src/_eddy_seek/"*.py; do
	install_one "$f" "$DEST/_eddy_seek/$(basename "$f")"
done

printf 'EddySeek: installed (%s) to %s\n' "$MODE" "$DEST"
printf '  eddy_seek.py\n'
printf '  _eddy_seek/*.py\n'
printf 'Next: add [eddy_seek] to printer.cfg and FIRMWARE_RESTART\n'
