"""Build a standalone Windows TotK-Setup folder from this patched source tree, reusing the pinned toolchain and third-party
binaries (python/, llvm/, bin/, data/, licenses/, runtime/deps/) from an existing TotK-Setup-0.5.0-windows folder.

Usage:
    python tools/make_patched_setup.py --base C:\\hezyn\\TotK-Setup-0.5.0-windows \\
        --gen C:\\hezyn\\totkgmg\\build-cache\\work\\gen \\
        --out C:\\hezyn\\TotK-Setup-0.5.0-patched-windows \\
        [--zip C:\\hezyn\\TotK-Setup-0.5.0-patched-windows.zip]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT))

import build_game  # noqa: E402
import build_xlate  # noqa: E402
import check_content  # noqa: E402
import make_release  # noqa: E402


def run(cmd, **kw):
    print('+', ' '.join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def copy_tree(src, dst, skip=lambda rel: False):
    for p in sorted(src.rglob('*')):
        rel = p.relative_to(src)
        if p.is_dir() or skip(rel.as_posix()):
            continue
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst / rel)


def check_v2_isa(build_dir, llvm):
    objdump = llvm / 'bin' / 'llvm-objdump.exe'
    objs = sorted((build_dir / 'obj').rglob('*.o'))
    if not objs:
        sys.exit(f'no .o files found under {build_dir / "obj"}')
    cmd = [sys.executable, ROOT / 'tools' / 'check_isa.py', '--objdump', objdump, *objs]
    print(f'+ check_isa.py ({len(objs)} v2 objects)', flush=True)
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f'check_isa.py FAILED:\n{r.stdout}\n{r.stderr}')
    print(r.stdout.strip() or f'check_isa: {len(objs)} v2 objects OK')


def verify_symbols(rt_dir, llvm):
    nm = llvm / 'bin' / 'llvm-nm.exe'
    for lib_name in ('libtotkrt.a', 'libtotkrt-v2.a'):
        lib = rt_dir / lib_name
        r = subprocess.run([str(nm), '--defined-only', str(lib)], capture_output=True, text=True, check=True)
        if 'gfx_last_flip_ns' not in r.stdout or 'vk_pipeline_graphics_gpl_async' not in r.stdout or 'gfx_shader_warm_svc' not in r.stdout:
            sys.exit(f'{lib_name}: missing patched symbols (gfx_last_flip_ns / vk_pipeline_graphics_gpl_async / gfx_shader_warm_svc)')
        blob = lib.read_bytes()
        for marker in (b'FEEDBACK_R32_COPY', b'linear-depth feedback snapshot', b'cache %s: %.1f MB copied', b'TOTK_PIPE_WAIT_MS', b'WATER_LOD_FIX'):
            if marker not in blob:
                sys.exit(f'{lib_name}: missing expected patch marker {marker!r}')
        print(f'symbols ok: {lib_name} ({lib.stat().st_size / (1 << 20):.2f} MiB)')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--base', type=Path, required=True, help='existing TotK-Setup-0.5.0-windows folder (pinned toolchain/deps)')
    ap.add_argument('--gen', type=Path, required=True, help='directory holding layout.h and layout.json (read-only)')
    ap.add_argument('--out', type=Path, required=True, help='output patched setup folder (replaced)')
    ap.add_argument('--version-str', default='0.5.0+afae80c9-patch9', help='VERSION string written to the release folder')
    ap.add_argument('--zip', type=Path, help='optional output zip path')
    args = ap.parse_args()

    t0 = time.time()
    base = args.base.resolve()
    gen = args.gen.resolve()
    out = args.out.resolve()

    if not (gen / 'layout.json').is_file() or not (gen / 'layout.h').is_file():
        sys.exit(f'{gen}: missing layout.h or layout.json')

    # Verify totk_xlate in base matches this source tree's translator identity
    xlate_id = (base / 'bin' / 'totk_xlate.id').read_text(encoding='ascii').strip()
    want_id = build_xlate.identity()
    if xlate_id != want_id:
        sys.exit(f'base bin/totk_xlate.id ({xlate_id[:16]}) != source ({want_id[:16]})')
    print(f'translator identity match: {want_id[:16]}')

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # 1. Copy pinned third-party/toolchain directories from base
    for folder in ('python', 'llvm', 'bin', 'data', 'licenses'):
        print(f'copying {folder}/ from base...', flush=True)
        copy_tree(base / folder, out / folder)
    print('copying runtime/deps/ from base...', flush=True)
    copy_tree(base / 'runtime' / 'deps', out / 'runtime' / 'deps')

    llvm = out / 'llvm'
    pgo_file = out / 'data' / 'pgo-v143.profdata'
    if not pgo_file.is_file():
        sys.exit(f'missing {pgo_file}')

    # 2. Compile complete libtotkrt.a (v3) and libtotkrt-v2.a (v2) from patched runtime sources
    work = Path(tempfile.mkdtemp(prefix='totk-patched-setup-'))
    try:
        rt = out / 'runtime'
        for cpu, lib_name in (('v3', 'libtotkrt.a'), ('v2', 'libtotkrt-v2.a')):
            bdir = work / f'rtlib-{cpu}'
            print(f'\n=== Building {lib_name} (cpu={cpu}) ===', flush=True)
            run([sys.executable, ROOT / 'tools' / 'build_game.py',
                 '--make-runtime-lib',
                 '--toolchain', llvm,
                 '--cpu', cpu,
                 '--version', 'v143',
                 '--deps', rt / 'deps',
                 '--ninja', out / 'bin' / 'ninja.exe',
                 '--gen', gen,
                 '--out', bdir,
                 '--pgo', pgo_file],
                env=make_release.clean_env(llvm / 'bin', out / 'bin'))
            if cpu == 'v2':
                check_v2_isa(bdir, llvm)
            shutil.copy2(bdir / 'libtotkrt.a', rt / lib_name)

        verify_symbols(rt, llvm)

        # 3. Copy runtime headers, .inc, .ico, and INSTALL_SIDE sources from this patched repo
        for p in sorted((ROOT / 'runtime').rglob('*')):
            if p.suffix in make_release.RUNTIME_SUFFIXES and p.is_file():
                rel = p.relative_to(ROOT / 'runtime')
                (rt / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, rt / rel)
        for name in sorted(build_game.INSTALL_SIDE):
            shutil.copy2(ROOT / 'runtime' / name, rt / name)

        # 4. Copy recomp/, tools/, installer/, notices, totk.ini.example, VERSION from this patched repo
        copy_tree(ROOT / 'recomp', out / 'recomp', lambda r: not r.endswith(('.py', '.txt')) or '__pycache__' in r)
        (out / 'tools').mkdir(exist_ok=True)
        for f in make_release.TOOLS_FILES:
            shutil.copy2(ROOT / 'tools' / f, out / 'tools' / f)
        for d in make_release.TOOLS_DIRS:
            copy_tree(ROOT / 'tools' / d, out / 'tools' / d, lambda r: '__pycache__' in r or r.endswith('.md'))
        copy_tree(ROOT / 'installer', out / 'installer', lambda r: '__pycache__' in r or r.startswith('launcher/'))
        shutil.copy2(ROOT / 'totk.ini.example', out / 'totk.ini.example')
        shutil.copy2(ROOT / 'LICENSE', out / 'LICENSE')
        shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', out / 'THIRD_PARTY_NOTICES.md')
        if (ROOT / 'PATCHES.md').is_file():
            shutil.copy2(ROOT / 'PATCHES.md', out / 'PATCHES.md')
        (out / 'VERSION').write_text(args.version_str + '\n', encoding='utf-8')

        # 5. Build TotK-Setup.exe launcher from this repo's launcher sources
        print('\n=== Building TotK-Setup.exe ===', flush=True)
        lwork = work / 'launcher'
        make_release.launcher_header(lwork / 'launcher_text.h')
        lenv = make_release.clean_env(llvm / 'bin')
        res = lwork / 'launcher_rc.o'
        run([llvm / 'bin' / 'llvm-windres.exe', '-I', ROOT / 'runtime', '-O', 'coff',
             ROOT / 'installer' / 'launcher' / 'launcher.rc', '-o', res], env=lenv)
        run([llvm / 'bin' / 'clang.exe', '-O2', '-mwindows', '-Wall', '-Wl,-s', '-I', lwork,
             ROOT / 'installer' / 'launcher' / 'launcher.c', res, '-o', out / 'TotK-Setup.exe'], env=lenv)

        # 6. Run official release verification checks
        print('\n=== Running check_content ===', flush=True)
        make_release.check_folder(out)

        print('\n=== Running bundle & GUI selftest ===', flush=True)
        chk = work / 'bundle'
        benv = make_release.clean_env(out / 'python')
        py = out / 'python' / 'python.exe'
        r = subprocess.run([str(py), '-m', 'installer', '--lang', 'en-US',
                            '--dest', str(chk / 'install'), '--temp', str(chk / 'temp')],
                           cwd=out, env=benv, capture_output=True, text=True, timeout=600)
        events = [json.loads(line) for line in r.stdout.splitlines() if line.startswith('{')]
        errors = [e['id'] for e in events if e['type'] == 'error']
        started = any(e['type'] == 'start' and e.get('release') == args.version_str for e in events)
        steps = [e['phase'] for e in events if e['type'] == 'progress']
        if errors != ['check.inputs_needed'] or not started or 'inputs' not in steps:
            sys.exit(f'bundle check failed: exit={r.returncode}, errors={errors}, started={started}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}')
        print(f'bundle check: installer host/release check PASS (release={args.version_str}, exit={r.returncode})')

        r = subprocess.run([str(py), '-m', 'installer.gui', '--selftest'],
                           cwd=out, env=benv, capture_output=True, text=True, timeout=300)
        if r.returncode:
            sys.exit(f'GUI selftest failed: exit={r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}')
        print('bundle check: installer GUI selftest PASS')

        print('\n=== Release folder sizes ===', flush=True)
        make_release.sizes(out)

        if args.zip:
            print(f'\n=== Writing zip {args.zip} ===', flush=True)
            make_release.write_zip(out, args.zip.resolve())

        print(f'\nDONE in {time.time() - t0:.1f} s -> {out}')
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
