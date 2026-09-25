#!/usr/bin/env python3
"""Generate a small local TimSim acquisition using a copied reference and synthetic proteins."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timsim', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True, help='DDA reference .d directory')
    parser.add_argument('--output', type=Path, required=True, help='New working directory')
    args = parser.parse_args()
    reference = args.reference.resolve(strict=True)
    if not reference.is_dir() or any(p.is_symlink() for p in reference.rglob('*')):
        raise ValueError('Reference must be a directory without linked members')
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    before = hashes(reference)
    shutil.copytree(reference, output / 'reference.d')
    rng = random.Random(42)
    fasta = ''.join('>synthetic_' + str(i) + '\n' + ''.join(rng.choice('ACDEFGHIKLMNPQRSTVWY') for _ in range(250)) + '\n' for i in range(30))
    (output / 'synthetic.fasta').write_text(fasta)
    (output / 'output').mkdir()
    config = '\n'.join([
        '[paths]', 'save_path = ' + json.dumps(str(output / 'output')),
        'reference_path = ' + json.dumps(str(output / 'reference.d')),
        'fasta_path = ' + json.dumps(str(output / 'synthetic.fasta')),
        '[experiment]', 'experiment_name = "spectarr-local-fixture"', 'acquisition_type = "DDA"',
        'gradient_length = 30.0', 'use_reference_layout = false', 'apply_fragmentation = true',
        '[digestion]', 'n_proteins = 30', 'num_peptides_total = 500', 'num_sample_peptides = 100', 'sample_seed = 42',
        '[performance]', 'num_threads = 2', 'batch_size = 64', 'frame_batch_size = 50', 'use_gpu = false',
    ]) + '\n'
    (output / 'config.toml').write_text(config)
    env = dict(os.environ)
    env.setdefault('IMSPY_CACHE_DIR', str(output / 'models'))
    env.setdefault('XDG_CACHE_HOME', str(output / 'cache'))
    env['OMP_NUM_THREADS'] = '2'
    with (output / 'simulation.log').open('w') as log:
        subprocess.run([str(args.timsim.resolve()), str(output / 'config.toml')], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    if hashes(reference) != before:
        raise RuntimeError('Reference changed during generation')
    dataset = output / 'output/spectarr-local-fixture/spectarr-local-fixture.d'
    if not (dataset / 'analysis.tdf').is_file() or not (dataset / 'analysis.tdf_bin').is_file():
        raise RuntimeError('TimSim did not produce a native dataset')
    (output / 'fixture.json').write_text(json.dumps({'reference': before, 'output': hashes(dataset),
        'config_sha256': hashlib.sha256(config.encode()).hexdigest(), 'synthetic_fasta_sha256': hashlib.sha256(fasta.encode()).hexdigest(),
        'note': 'Record the environment lock alongside this result. Sampling seed does not promise byte-identical model output.'}, indent=2) + '\n')
    print(dataset)


if __name__ == '__main__':
    main()
