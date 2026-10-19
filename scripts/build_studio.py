"""Build the two React interfaces used by the owned 3D studio."""
from pathlib import Path
import shutil
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    npm = shutil.which('npm.cmd' if __import__('os').name == 'nt' else 'npm')
    if npm is None:
        raise SystemExit('Install Node.js/npm before building the studio interfaces.')
    for folder in (root, root / 'vulcan/studio_native/gepetto'):
        subprocess.run([npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=folder, check=True)
        subprocess.run([npm, 'run', 'build'], cwd=folder, check=True)


if __name__ == '__main__':
    main()
