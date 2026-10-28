"""Import independently verified historical offers JSON."""
import argparse
from pathlib import Path
from app.data.snapshot_catalog import SnapshotCatalogProvider

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    print('Imported', SnapshotCatalogProvider().import_file(args.source), 'snapshots')
