"""Keep production constraints consistent with the backend lockfile."""
from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path


def normalize(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Refresh overlapping constraints from the backend lock')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    lock = tomllib.loads((root / 'backend/uv.lock').read_text())
    versions = {normalize(package['name']): package['version'] for package in lock['package']}
    path = root / 'constraints.txt'
    pins = dict(line.strip().split('==', 1) for line in path.read_text().splitlines() if line.strip() and not line.startswith('#'))
    mismatches = []
    for name, version in pins.items():
        locked = versions.get(normalize(name))
        if locked and version != locked:
            mismatches.append(f'{name}: constraints={version}, backend lock={locked}')
            if args.write:
                pins[name] = locked
    backend = next(package for package in lock['package'] if package['name'] == 'spectarr')
    required = set()
    pending = [dependency['name'] for dependency in backend['dependencies']]
    packages = {package['name']: package for package in lock['package']}
    while pending:
        name = pending.pop()
        if name in required:
            continue
        required.add(name)
        pending.extend(dependency['name'] for dependency in packages[name].get('dependencies', []))
    normalized_pins = {normalize(name) for name in pins}
    for name in sorted(required):
        if normalize(name) not in normalized_pins:
            mismatches.append(f'{name}: missing production constraint')
            if args.write:
                pins[name] = versions[normalize(name)]
    if args.write:
        path.write_text(''.join(f'{name}=={pins[name]}\n' for name in sorted(pins, key=str.casefold)))
        print('Updated production constraints from backend/uv.lock')
        return 0
    if mismatches:
        print('\n'.join(mismatches))
        print('Run python3 scripts/check_dependencies.py --write after updating the backend lock')
        return 1
    print('Production API constraints match the backend lock')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
