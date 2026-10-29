"""Import verified historical offer observations."""

import argparse
from pathlib import Path

from app.data.snapshot_catalog import SnapshotCatalogProvider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    imported = SnapshotCatalogProvider().import_file(args.source)
    print("Imported", imported, "snapshots")


if __name__ == "__main__":
    main()
