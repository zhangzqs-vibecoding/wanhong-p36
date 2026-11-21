#!/usr/bin/env python3
"""Build a FAT32 SD card image from the extracted P36 files."""

import argparse
import os
from pathlib import Path
import subprocess
from tempfile import NamedTemporaryFile


def build_image(source, image):
    firmware = (source / "sword_elfd.bin").read_bytes().lower()
    entries = sorted(source.iterdir())
    system_files = []
    for entry in entries:
        prefix = b"d:/sysfiles/" + entry.name.encode("gb18030").lower()
        terminator = b"/" if entry.is_dir() else b"\x00"
        if prefix + terminator in firmware:
            system_files.append(entry)
    root_entries = [entry for entry in entries if entry not in system_files]
    if image.exists():
        raise FileExistsError(image)

    with NamedTemporaryFile(dir=image.parent, prefix=".p36-sd-") as temporary:
        temporary.truncate(4 * 1024 ** 3)
        subprocess.run(["mkfs.fat", "-F", "32", "-s", "64", "-n", "P36", temporary.name], check=True)
        for files, destination in ((root_entries, "::/"), (system_files, "::/SYSFILES/")):
            if not files:
                continue
            if destination != "::/":
                subprocess.run(["mmd", "-i", temporary.name, destination.rstrip("/")], check=True)
            subprocess.run(
                ["mcopy", "-s", "-p", "-i", temporary.name, *files, destination],
                check=True,
            )
        subprocess.run(["fsck.fat", "-n", temporary.name], check=True)
        os.link(temporary.name, image)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "sdcard")
    args = parser.parse_args()
    build_image(args.source, args.image)
