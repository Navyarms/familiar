"""Dev helper: download whatever the current tier is missing, with a console progress line.

Usage: uv run python scripts/download_models.py [--tier T1]
"""

import argparse
import asyncio
import sys

from familiar_brain.config import Paths, Settings
from familiar_brain.models import ModelManager


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default=None)
    a = ap.parse_args()
    paths = Paths.default().ensure()
    settings = Settings.load(paths.settings_file)
    if a.tier:
        settings.tier_override = a.tier
    mm = ModelManager(paths, settings)
    print(f"tier {mm.tier} ({mm.tier_reason})")
    for row in mm.status():
        print(f"  {'ok ' if row['present'] else '-- '} {row['name']}  {row['bytes_total']/1e6:,.0f} MB")

    async def progress(item, done, total, bps, odone, ototal):
        sys.stdout.write(f"\r  {item}: {done/1e6:,.0f}/{total/1e6:,.0f} MB  {bps/1e6:,.1f} MB/s   overall {100*odone/max(1,ototal):5.1f}%   ")
        sys.stdout.flush()

    await mm.download_missing(progress)
    print("\ndone; missing now:", [i.name for i in mm.missing()])


if __name__ == "__main__":
    asyncio.run(main())
