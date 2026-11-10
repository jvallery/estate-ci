#!/usr/bin/env python3
"""Qualify the campaign tests present at the exact checkout, using private PostgreSQL."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET

CANDIDATES = (
    'tests/test_c0_release_rehearsal.py',
    'tests/test_apply_migrations_runner.py',
    'tests/test_fixed_panel.py',
    'tests/test_resource_ledger.py',
    'tests/test_scorecard_semantics.py',
    'tests/test_forecast_verification_contract.py',
    'tests/test_public_band_lineage.py',
)
# jvallery/agents#5994: the dashboard program's database tests (the M1 to M6 grant checks).
# Each file gets its own fresh server with TimescaleDB and pgvector, as db/schema.sql needs.
DASHBOARD_GLOB = 'test_dashboard_db_*.py'
# VERDIFY_CAMPAIGN_SELECT: unset or empty runs everything; dashboard-db keeps only the glob
# (verdify-platform-ci's campaign step). Any other value is refused.
SELECTIONS = ('', 'dashboard-db')
DASHBOARD_DATABASE = 'verdify_test_dashboard'
EXTENSION_DIR = Path('/usr/share/postgresql/16/extension')
PGDG_KEY = ('https://www.postgresql.org/media/keys/ACCC4CF8.asc',
            '0144068502a1eddd2a0280ede10ef607d1ec592ce819940991203941564e8e76')
# The live extension (verdify-platform deploy/k8s/cnpg/image/Dockerfile names this bookworm
# build), from Timescale's signed packagecloud repository.
TIMESCALEDB_VERSION = '2.25.2'
TIMESCALEDB_DEB = '2.25.2~debian12-1613'
TIMESCALE_KEY = ('https://packagecloud.io/timescale/timescaledb/gpgkey',
                 'eb53bd3dc31e06fbf1ed769139f12b1fffaf1958a96c9b4f9727c19aada83321')


def selection(select: str | None = None) -> tuple[list[str], list[str]]:
    """The test files to run and the requested candidates absent from this source."""
    select = os.environ.get('VERDIFY_CAMPAIGN_SELECT', '') if select is None else select
    if select not in SELECTIONS:
        raise ValueError(f'unknown VERDIFY_CAMPAIGN_SELECT {select!r}; expected one of {SELECTIONS}')
    dashboard = [path.as_posix() for path in sorted(Path('tests').glob(DASHBOARD_GLOB))]
    if select == 'dashboard-db':
        return dashboard, []
    present = [name for name in CANDIDATES if Path(name).is_file()]
    return present + dashboard, [name for name in CANDIDATES if name not in present]


def is_dashboard(name: str) -> bool:
    return Path(name).match('tests/' + DASHBOARD_GLOB)


def pinned_download(url: str, sha256: str, attempts: int = 3) -> bytes:
    # CI pods sometimes stall in the TLS handshake; retry before failing the step.
    for attempt in range(attempts):
        try:
            raw = urllib.request.urlopen(url, timeout=30).read(65536)
            break
        except OSError:
            if attempt + 1 == attempts:
                raise
            time.sleep(5 * 2 ** attempt)
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise RuntimeError(f'signing key at {url} does not match the pinned authority')
    return raw


def postgres_bin(workspace: Path, installed=Path('/usr/lib/postgresql/16/bin'), timescale=False,
                 extension_dir=EXTENSION_DIR) -> Path:
    extensions = ('pgcrypto', 'timescaledb', 'vector') if timescale else ('pgcrypto',)
    if (installed / 'initdb').is_file() and all((extension_dir / f'{name}.control').is_file()
                                                 for name in extensions):
        return installed
    # Migration 217 reads pg_auth_members.inherit_option, introduced in PG16.
    # Resolve signed Debian/PGDG packages in private state, download only, then
    # extract without running package installation scripts or starting services.
    package_root = workspace / 'postgres-toolchain'
    package_root.mkdir()
    lists = package_root / 'lists'
    (lists / 'partial').mkdir(parents=True)
    archives = package_root / 'archives'
    (archives / 'partial').mkdir(parents=True)
    key = package_root / 'pgdg.asc'
    key.write_bytes(pinned_download(*PGDG_KEY))
    repositories = [f'deb [signed-by={key}] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main']
    packages = ['postgresql-16', 'postgresql-client-16']
    if timescale:
        timescale_key = package_root / 'timescaledb.asc'
        timescale_key.write_bytes(pinned_download(*TIMESCALE_KEY))
        repositories.append(f'deb [signed-by={timescale_key}] '
                            'https://packagecloud.io/timescale/timescaledb/debian/ bookworm main')
        packages += [f'timescaledb-2-postgresql-16={TIMESCALEDB_DEB}',
                     f'timescaledb-2-loader-postgresql-16={TIMESCALEDB_DEB}',
                     'postgresql-16-pgvector']
    sources = package_root / 'pgdg.list'
    sources.write_text(''.join(line + '\n' for line in repositories))
    apt = ['apt-get', '-o', f'Dir::State::lists={lists}', '-o', f'Dir::Cache::archives={archives}',
           '-o', f'Dir::Etc::sourcelist={sources}', '-o', 'Debug::NoLocking=true',
           '-o', 'Acquire::AllowInsecureRepositories=false', '-o', 'Acquire::Retries=5',
           '-o', 'Acquire::http::Timeout=20', '-o', 'Acquire::https::Timeout=20']
    subprocess.run([*apt, 'update'], check=True, cwd=package_root)
    subprocess.run([*apt, '--download-only', '--reinstall', '--no-install-recommends', '-y',
                    'install', *packages], check=True, cwd=package_root)
    root = package_root / 'root'
    for package in archives.glob('*.deb'):
        subprocess.run(['dpkg-deb', '-x', str(package), str(root)], check=True)
    binaries = root / 'usr/lib/postgresql/16/bin'
    extension = root / 'usr/share/postgresql/16/extension'
    if not (binaries / 'initdb').is_file() or not (extension / 'pgcrypto.control').is_file():
        raise RuntimeError('private PostgreSQL 16/pgcrypto toolchain is incomplete')
    if timescale:
        control = extension / 'timescaledb.control'
        if (not control.is_file() or f"default_version = '{TIMESCALEDB_VERSION}'" not in control.read_text()
                or not (extension / 'vector.control').is_file()):
            raise RuntimeError(f'private TimescaleDB {TIMESCALEDB_VERSION}/pgvector toolchain is incomplete')
    libraries = sorted(str(p) for p in (root / 'usr/lib').glob('*-linux-gnu') if p.is_dir())
    os.environ['LD_LIBRARY_PATH'] = os.pathsep.join(libraries)
    subprocess.run([str(binaries / 'postgres'), '--version'], check=True)
    return binaries


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def dashboard_server(binaries: Path, workspace: Path, env: dict[str, str]):
    """A fresh trust-auth loopback server for one dashboard file, removed afterwards."""
    root = Path(tempfile.mkdtemp(prefix='dashboard-db-', dir=workspace))
    data, sockets = root / 'data', root / 'socket'
    sockets.mkdir()
    try:
        subprocess.run([str(binaries / 'initdb'), '-D', str(data), '-U', 'postgres', '--auth=trust'],
                       check=True, env=env, stdout=subprocess.DEVNULL)
        port = free_port()
        options = ['-c', 'listen_addresses=127.0.0.1', '-c', f'port={port}',
                   '-c', f'unix_socket_directories={sockets}',
                   '-c', 'shared_preload_libraries=timescaledb',
                   '-c', 'timescaledb.telemetry_level=off']
        try:
            subprocess.run([str(binaries / 'pg_ctl'), '-D', str(data), '-l', str(root / 'server.log'),
                            '-o', shlex.join(options), '-w', '-t', '60', 'start'], check=True, env=env)
        except subprocess.CalledProcessError:
            # The private server's own startup log; it holds no credential.
            if (root / 'server.log').is_file():
                print((root / 'server.log').read_text(errors='replace')[-4000:], flush=True)
            raise
        subprocess.run([str(binaries / 'createdb'), '-h', '127.0.0.1', '-p', str(port), '-U', 'postgres',
                        DASHBOARD_DATABASE], check=True, env=env)
        yield f'postgresql://postgres@127.0.0.1:{port}/{DASHBOARD_DATABASE}'
    finally:
        # Also covers a start that timed out after the postmaster came up.
        if (data / 'postmaster.pid').exists():
            subprocess.run([str(binaries / 'pg_ctl'), '-D', str(data), '-m', 'fast', '-w', '-t', '60', 'stop'],
                           check=False, env=env)
        shutil.rmtree(root, ignore_errors=True)


def fold(junit: Path, report: dict) -> dict[str, int]:
    counts = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    if junit.is_file():
        for case in ET.parse(junit).iter('testcase'):
            counts['tests'] += 1
            for tag, key in (('failure', 'failures'), ('error', 'errors'), ('skipped', 'skipped')):
                if case.find(tag) is not None:
                    counts[key] += 1
    for key, value in counts.items():
        report[key] += value
    return counts


def main():
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    select = os.environ.get('VERDIFY_CAMPAIGN_SELECT', '')
    try:
        selected, missing = selection(select)
    except ValueError as error:
        print(f'Campaign qualification refused: {error}', flush=True)
        return 2
    report = {'schema':'verdify.campaign-ci/1', 'source_revision':source,
              'selected':selected, 'not_in_source':missing, 'tests':0, 'failures':0,
              'errors':0, 'skipped':0, 'qualified':False}
    destination = Path(os.environ.get('CI_TEST_REPORT', '/workspace/ci-test-report.json'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not selected:
            print('No campaign tests in this source revision; no campaign qualification claimed.')
            return 0
        dashboard = [name for name in selected if is_dashboard(name)]
        others = [name for name in selected if not is_dashboard(name)]
        with tempfile.TemporaryDirectory(prefix='verdify-campaign-') as temporary:
            workspace = Path(temporary)
            binaries = postgres_bin(workspace, timescale=bool(dashboard))
            env = {key: value for key,value in os.environ.items()
                   if not key.startswith(('PG', 'DB_', 'POSTGRES_'))
                   and key not in ('DATABASE_URL', 'VERDIFY_ISOLATED_TEST_DSN')}
            env['SCORECARD_TEST_PG_BIN'] = str(binaries)
            env['BAND_TRACE_TEST_PG_BIN'] = str(binaries)
            env['FIXED_PANEL_TEST_PG_BIN'] = str(binaries)
            env['PATH'] = str(binaries) + os.pathsep + env['PATH']
            print('CI selection: ' + (select or 'all'), flush=True)
            print('CI selected: ' + ', '.join(selected), flush=True)
            print('CI not in source: ' + ', '.join(missing), flush=True)
            returncodes = []
            if others:
                # Fixtures init their own trust-auth UNIX socket and disable TCP;
                # no production database address or credential reaches the tests.
                junit = workspace / 'junit.xml'
                result = subprocess.run(['uv', 'run', 'pytest', '-q', *others, f'--junitxml={junit}'], env=env)
                returncodes.append(result.returncode)
                print(f'CI result {", ".join(others)}: exit {result.returncode} {fold(junit, report)}', flush=True)
            for index, name in enumerate(dashboard):
                # Only dashboard files get a DSN: a fresh private loopback server each,
                # in the verdify_test_ shape tests/conftest.py isolated_mutation_test_dsn() accepts.
                junit = workspace / f'junit-dashboard-{index}.xml'
                try:
                    with dashboard_server(binaries, workspace, env) as dsn:
                        result = subprocess.run(['uv', 'run', 'pytest', '-q', name, f'--junitxml={junit}'],
                                                env={**env, 'VERDIFY_ISOLATED_TEST_DSN': dsn})
                    returncodes.append(result.returncode)
                    print(f'CI result {name}: exit {result.returncode} {fold(junit, report)}', flush=True)
                except (OSError, subprocess.CalledProcessError) as error:
                    returncodes.append(1)
                    report['errors'] += 1
                    print(f'CI result {name}: private TimescaleDB server failed: {error}', flush=True)
            report['qualified'] = (all(code == 0 for code in returncodes) and report['tests'] > 0
                                   and not any(report[key] for key in ('failures','errors','skipped')))
            if not report['qualified']:
                print('Campaign qualification failed: failures, missing results or skipped required tests.', flush=True)
                return next((code for code in returncodes if code), 1)
            return 0
    finally:
        destination.write_text(json.dumps(report, sort_keys=True))
        print('VERDIFY_CAMPAIGN_REPORT=' + json.dumps(report, sort_keys=True), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
