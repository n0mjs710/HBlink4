"""Interactive bootstrap for the initial BuenoDMR administrator."""

import argparse
import getpass
import sys
from pathlib import Path

from .admin.security import password_hash
from .admin.storage import AdminStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="BuenoDMR admin bootstrap")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin")
    create.add_argument("--username", required=True)
    create.add_argument("--dmr-id", required=True, type=int)
    create.add_argument("--replace", action="store_true", help="Rotate the initial administrator password")
    args = parser.parse_args(argv)

    if args.username.upper() != "PY2DES" or args.dmr_id != 7242870:
        parser.error("Phase 1 permits only PY2DES (DMR ID 7242870) as administrator")
    store = AdminStore(Path(__file__).parent / "data")
    store.initialize()
    exists = store.get_admin("PY2DES") is not None
    if exists and not args.replace:
        parser.error("Administrator already exists. Use --replace for deliberate rotation")
    if exists and args.replace:
        if input("Type PY2DES to confirm password rotation: ") != "PY2DES":
            parser.error("Rotation cancelled")
    if not sys.stdin.isatty():
        parser.error("An interactive terminal is required for password entry")
    password = getpass.getpass("Admin password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 12 or password != confirmation:
        parser.error("Passwords must match and contain at least 12 characters")
    store.create_or_replace_admin("PY2DES", 7242870, password_hash(password), replace=exists)
    print("Administrator PY2DES ready. Existing sessions were invalidated if rotated.")


if __name__ == "__main__":
    main()
