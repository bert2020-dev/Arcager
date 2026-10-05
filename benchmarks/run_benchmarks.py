#!/usr/bin/env python3
"""Run Arcager's reproducible 24-scenario benchmark corpus.

The benchmark consumes the canonical fixture files shipped in benchmarks/fixtures.
It never silently regenerates scenarios into a temporary directory.
"""
from __future__ import annotations
import argparse, csv, json, os, platform, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parents[1]
BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(BENCH))
import arcager
from examples import CASES, build_manifest

V3 = BENCH / 'baselines' / '3.2.3' / 'arcager.py'
FIXTURES = BENCH / 'fixtures'


def _probe_python(command, modules=()):
    """Return interpreter details for a command, or None when unavailable."""
    try:
        mods_expr = repr(tuple(modules))
        code = (
            "import sys, importlib.util\n"
            "print(sys.executable)\n"
            "print(sys.version.split()[0])\n"
            f"mods={mods_expr}\n"
            "print('|'.join(m + ('=1' if importlib.util.find_spec(m) else '=0') for m in mods))\n"
        )
        r = subprocess.run(list(command) + ['-c', code], capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            return None
        lines = r.stdout.splitlines()
        if len(lines) < 3:
            return None
        mods = {}
        for part in lines[2].split('|') if lines[2] else []:
            name, val = part.split('=', 1)
            mods[name] = val == '1'
        return {'command': list(command), 'executable': lines[0], 'version': lines[1], 'modules': mods,
                'stderr': r.stderr.strip()}
    except Exception:
        return None


def resolve_benchmark_python(required_modules=(), explicit=None):
    """Pick a Python interpreter capable of the benchmark's required optional features.

    The driver may itself have been launched by a Windows Store alias or another
    Python without optional packages. We therefore probe the current interpreter
    and common platform launchers before declaring a dependency unavailable.
    """
    candidates = []
    if explicit:
        candidates.append([explicit])
    candidates.append([sys.executable])
    if os.name == 'nt':
        candidates += [['py', '-3.13'], ['py', '-3'], ['python']]
    else:
        candidates += [['python3'], ['python']]
    seen = set()
    tried = []
    for cmd in candidates:
        key = tuple(cmd)
        if key in seen:
            continue
        seen.add(key)
        info = _probe_python(cmd, ('cryptography','brotli','PIL'))
        if info is None:
            tried.append({'command': cmd, 'available': False})
            continue
        tried.append(info)
        if all(info['modules'].get(m, False) for m in required_modules):
            info['tried'] = tried
            return info
    details = []
    for t in tried:
        cmd = ' '.join(t['command'])
        if t.get('available') is False:
            details.append(f'  {cmd}: unavailable')
        else:
            missing = [m for m in required_modules if not t['modules'].get(m, False)]
            details.append(f"  {cmd}: {t['executable']} Python {t['version']} missing {', '.join(missing) or 'none'}")
    req = ', '.join(required_modules) or 'none'
    raise SystemExit(
        'No benchmark Python interpreter satisfies the required optional libraries.\n'
        f'Required: {req}\n' + '\n'.join(details) +
        '\nUse --python C:\\path\\to\\python.exe (Windows) or the equivalent path on your platform.'
    )


def fmt_mb(n): return n / (1024*1024)

def fmt_pct(n): return f'{n:.2f}%'

def fmt_ratio(src, out): return src / out if out else 0.0


def timed_run(cmd, cwd):
    t0=time.perf_counter()
    r=subprocess.run(cmd,cwd=cwd,capture_output=True,text=True)
    sec=time.perf_counter()-t0
    if r.returncode:
        detail=(r.stdout+r.stderr).strip()
        rendered=' '.join(str(x) for x in cmd)
        raise RuntimeError(f'command failed ({r.returncode}): {rendered}\n{detail}')
    return sec, r


def case_root(case_dir: Path) -> Path:
    site = case_dir / 'site'
    return site if site.is_dir() else case_dir


def hidden_secret(case_id: str, case_dir: Path) -> Optional[Path]:
    if case_id == '14-secret-7z-and-csv':
        p=case_dir/'private-data.7z'
        if not p.exists():
            raise FileNotFoundError(p)
        return p
    return None


def source_size(case_dir: Path, case_id: str) -> int:
    root=case_root(case_dir)
    total=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    secret=hidden_secret(case_id,case_dir)
    if secret: total += secret.stat().st_size
    return total


def source_site_size(case_dir: Path) -> int:
    root=case_root(case_dir)
    return sum(p.stat().st_size for p in root.rglob('*') if p.is_file())


def source_asset_size(case_dir: Path) -> int:
    root=case_root(case_dir)
    return sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and p.name != 'index.html')


def _package_payload_text(package: Path) -> str:
    text=package.read_text(encoding='utf-8')
    m=re.search(r'<script[^>]*id=[\'"]__pk[\'"][^>]*>([\s\S]*?)</script>', text, re.I)
    if not m:
        raise RuntimeError('v4 package payload script not found')
    return m.group(1)


def v4_accounting(packed_path: Path):
    content,attrs,enc_data,encrypted=arcager.read_packed_v4(packed_path)
    if encrypted:
        raise RuntimeError('encrypted package accounting is unavailable without decrypting the container')
    parsed=arcager.V4ContainerBuilder.parse(enc_data)
    payload=parsed['payload']
    compressed_total=0
    decisions=[]
    for sid,s in enumerate(parsed['streams']):
        start=s['off']
        end=parsed['streams'][sid+1]['off'] if sid+1<len(parsed['streams']) else len(payload)
        stored=len(payload[start:end])
        raw=int(s['raw_size'])
        compressed_total += stored
        algo=int(s['algo'])
        decisions.append({'stream':sid,'mime':s['mime'],'algo':algo,'raw_bytes':raw,'stored_bytes':stored,'saved_bytes':raw-stored,
                          'decision':'compressed' if algo else 'stored'})
        if algo and stored >= raw:
            raise RuntimeError(f'solid stream {sid} was kept despite being non-beneficial: {stored} >= {raw}')
    encoded=_package_payload_text(packed_path)
    main_z85_bytes=len(encoded.encode('ascii'))
    content=packed_path.read_text(encoding='utf-8')
    hidden_matches=list(re.finditer(r'<script[^>]*id=[\"\']__hid[\"][^>]*>([\s\S]*?)</script>', content, re.I))
    hidden_z85_bytes=sum(len(m.group(1).encode('ascii')) for m in hidden_matches)
    hidden_block_bytes=sum(len(m.group(0).encode('utf-8')) for m in hidden_matches)
    hidden_payload_bytes=0
    for m in hidden_matches:
        attrs=dict((k.lower(),v or v2) for k,v,v2 in re.findall(r'([\w-]+)=(?:"([^"]*)"|\'([^\']*)\')',m.group(0)))
        hidden_payload_bytes += int(attrs.get('data-n','0'))
    total_z85_bytes=main_z85_bytes+hidden_z85_bytes
    shell_bytes=packed_path.stat().st_size-total_z85_bytes
    metadata_bytes=len(enc_data)-arcager.V4_HEADER_SIZE-len(payload)
    raw_resource_bytes=sum(int(s['raw_size']) for s in parsed['streams'])
    return {
        'container_bytes':len(enc_data),
        'payload_bytes':len(payload),
        'raw_resource_bytes':raw_resource_bytes,
        'metadata_bytes':metadata_bytes,
        'z85_bytes':main_z85_bytes,
        'hidden_z85_bytes':hidden_z85_bytes,
        'hidden_payload_bytes':hidden_payload_bytes,
        'hidden_block_bytes':hidden_block_bytes,
        'z85_overhead_bytes':(total_z85_bytes-len(enc_data)-hidden_payload_bytes),
        'shell_bytes':shell_bytes,
        'streams':len(parsed['streams']),
        'logical':len(parsed['logical']),
        'physical':len(parsed['physical']),
        'duplicates':max(0,len(parsed['logical'])-len(parsed['physical'])),
        'solid_decisions':decisions,
        'z85_overhead_pct':((total_z85_bytes)/(len(enc_data)+hidden_payload_bytes)-1)*100 if (enc_data or hidden_payload_bytes) else 0.0,
        'stream_reduction_pct':(1-len(payload)/raw_resource_bytes)*100 if raw_resource_bytes else 0.0,
        'container_metadata_pct':metadata_bytes/len(enc_data)*100 if enc_data else 0.0,
        'shell_overhead_pct':shell_bytes/len(enc_data)*100 if enc_data else 0.0,
    }


def _normalize_v4_tokens(data: bytes, logical_paths: dict) -> bytes:
    """Undo Arcager's intentional resource-token rewrite for benchmark comparison."""
    text = data.decode('utf-8', errors='strict')
    def repl(m):
        idx = int(m.group(1))
        suffix = m.group(2) or ''
        path = logical_paths.get(idx)
        return path + suffix if path is not None else m.group(0)
    text = re.sub(r'arcager-res:(\d+)([?#][^\s"\'<>\)\]]*)?', repl, text)
    return text.encode('utf-8')


def verify_v4_asset_roundtrip(package: Path, case_dir: Path):
    content,attrs,C,key=arcager.parse_pack_for_cli(str(package))
    root=case_root(case_dir)
    failures=[]
    cache={}
    logical_paths={i:n['path'] for i,n in enumerate(C['logical'])}
    exact=0; transformed=0
    for i,node in enumerate(C['logical']):
        logical=node['path']
        if logical == 'index.html' or (node['flags'] & 8):
            continue
        source=root/logical
        if not source.exists():
            failures.append(f'missing source: {logical}')
            continue
        recovered=arcager.materialize_logical(C,i,cache)
        expected=source.read_bytes()
        if recovered == expected:
            exact += 1
            continue
        # Merge mode intentionally rewrites resource references to arcager-res:N
        # so the runtime can remap them to final physical resources. Undo only
        # that known transformation and require exact content after normalization.
        try:
            normalized=_normalize_v4_tokens(recovered, logical_paths)
        except UnicodeDecodeError:
            normalized=None
        if normalized == expected:
            transformed += 1
            continue
        failures.append(f'content mismatch: {logical}')
    return {'ok':not failures,'failures':failures,'logical_checked':exact+transformed,
            'logical_exact':exact,'logical_transformed':transformed}


def pack_and_decode(python_cmd, script: Path, version: str, case_id: str, case_dir: Path, source_bytes: int, codec: str, *, verify=True):
    with tempfile.TemporaryDirectory(prefix='arcager-bench-case-') as td:
        td=Path(td); packed=td/f'packed-{version}-{codec}.html'; decoded=td/'decoded.html'
        root=case_root(case_dir)
        secret=hidden_secret(case_id,case_dir)
        if version=='3.2.3':
            cmd=[*python_cmd,str(script),'-M',str(root),'-f','-o',str(packed)]
            if codec=='brotli': cmd.append('--brotli')
        else:
            cmd=[*python_cmd,str(script),'-M',str(root),'-f','-o',str(packed),'-c',codec]
        if secret:
            cmd += ['-b',str(secret),'hidden','--bundle-password','benchmark-hidden-password']
        enc_sec, enc_result=timed_run(cmd, HERE if version=='4.0.0' else script.parent.parent)
        dec_cmd=[*python_cmd,str(script),'-u','-f','-o',str(decoded),str(packed)]
        dec_sec, dec_result=timed_run(dec_cmd, HERE if version=='4.0.0' else script.parent.parent)
        row={
            'version':version,'codec':codec,'case':case_id,
            'source_bytes':source_bytes,'source_site_bytes':source_site_size(case_dir),'source_asset_bytes':source_asset_size(case_dir),
            'package_bytes':packed.stat().st_size,'decoded_root_bytes':decoded.stat().st_size if decoded.exists() else 0,
            'compression_ratio':fmt_ratio(source_bytes,packed.stat().st_size),
            'final_html_delta_pct':(packed.stat().st_size/source_bytes-1)*100,
            'encode_seconds':enc_sec,'decode_seconds':dec_sec,
            'encode_MB_s':fmt_mb(source_bytes)/enc_sec if enc_sec else None,
            'decode_MB_s':fmt_mb(source_bytes)/dec_sec if dec_sec else None,
            'stdout':enc_result.stdout.strip(),'stderr':(enc_result.stderr+dec_result.stderr+dec_result.stdout).strip(),
        }
        if version=='4.0.0' and not (secret and False):
            row.update(v4_accounting(packed))
            if verify:
                vr=verify_v4_asset_roundtrip(packed,case_dir)
                if not vr['ok']:
                    raise RuntimeError('; '.join(vr['failures'][:5]))
                row['asset_roundtrip_ok']=vr['ok']; row['logical_checked']=vr['logical_checked']; row['logical_exact']=vr['logical_exact']; row['logical_transformed']=vr['logical_transformed']
            else:
                row['asset_roundtrip_ok']=None
        return row


def run_v4_capability_variant(python_cmd, script, case_id, case_dir, source_bytes, codec, *, lossy=False, strip=False, encrypt=False, hidden=False):
    with tempfile.TemporaryDirectory(prefix='arcager-cap-') as td:
        td=Path(td); packed=td/'packed.html'; decoded=td/'decoded.html'
        root=case_root(case_dir)
        cmd=[*python_cmd,str(script),'-M',str(root),'-f','-o',str(packed),'-c',codec]
        if lossy: cmd.append('lossy')
        if strip: cmd.append('--strip-metadata')
        if encrypt: cmd += ['-E','--password','benchmark-password']
        if hidden:
            secret=hidden_secret(case_id,case_dir)
            if secret: cmd += ['-b',str(secret),'hidden','--bundle-password','hidden-password']
        enc_sec,_=timed_run(cmd,HERE)
        dec_args=[*python_cmd,str(script),'-u','-f','-o',str(decoded),str(packed)]
        if encrypt: dec_args += ['--password','benchmark-password']
        dec_sec,_=timed_run(dec_args,HERE)
        row={'codec':codec,'lossy':lossy,'strip_metadata':strip,'encrypt':encrypt,'hidden_bundle':hidden,
             'case':case_id,'source_bytes':source_bytes,'package_bytes':packed.stat().st_size,
             'compression_ratio':fmt_ratio(source_bytes,packed.stat().st_size),'final_html_delta_pct':(packed.stat().st_size/source_bytes-1)*100,
             'encode_seconds':enc_sec,'decode_seconds':dec_sec,
             'encode_MB_s':fmt_mb(source_bytes)/enc_sec if enc_sec else None,'decode_MB_s':fmt_mb(source_bytes)/dec_sec if dec_sec else None}
        if not encrypt:
            row.update(v4_accounting(packed))
        return row


def manifest_cases(fixtures: Path):
    manifest=fixtures/'manifest.json'
    if not manifest.exists():
        raise SystemExit(f'benchmark fixtures are not installed: {fixtures}\nRun: python benchmarks/examples.py')
    data=json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('case_count') != 24:
        raise SystemExit(f'fixture manifest is incomplete: expected 24 cases, found {data.get("case_count")}')
    return {x['id']:x for x in data['cases']}


def main():
    ap=argparse.ArgumentParser(description='Arcager 3.2.3 vs 4.0.0 reproducible 24-case benchmark')
    ap.add_argument('--out',default='benchmarks/results')
    ap.add_argument('--fixtures',default=str(FIXTURES))
    ap.add_argument('--cases',default='all',help='case ID, comma-separated IDs, or all')
    ap.add_argument('--no-v3',action='store_true',help='skip bundled 3.2.3 baseline')
    ap.add_argument('--brotli-samples',action='store_true',help='also run 4.0 Brotli on selected cases')
    ap.add_argument('--v3-brotli',action='store_true',help='also run 3.2.3 Brotli (slow)')
    ap.add_argument('--skip-roundtrip-check',action='store_true',help='skip v4 asset preservation checks')
    ap.add_argument('--python',dest='python_path',help='Python executable to use for all benchmark subprocesses')
    ap.add_argument('--capabilities',action='store_true',help='also run slow lossy/metadata/encryption/hidden-bundle variants on selected cases')
    ns=ap.parse_args()
    required_modules=[]
    selected_probe=[cid.strip() for cid in ns.cases.split(',')] if ns.cases!='all' else [cid for cid,_,_ in CASES]
    if '14-secret-7z-and-csv' in selected_probe or ns.capabilities:
        required_modules.append('cryptography')
    if ns.brotli_samples or ns.v3_brotli:
        required_modules.append('brotli')
    if ns.capabilities:
        required_modules.append('PIL')
    python_info=resolve_benchmark_python(required_modules, ns.python_path)
    python_cmd=python_info['command']
    print(f"Benchmark Python: {python_info['executable']} (Python {python_info['version']})", file=sys.stderr, flush=True)
    if Path(sys.executable).resolve() != Path(python_info['executable']).resolve():
        print(f"  driver Python differs: {sys.executable}", file=sys.stderr, flush=True)
    fixtures=Path(ns.fixtures).resolve()
    cases_manifest=manifest_cases(fixtures)
    selected_ids=[cid for cid,_,_ in CASES] if ns.cases=='all' else ns.cases.split(',')
    unknown=sorted(set(selected_ids)-{cid for cid,_,_ in CASES})
    if unknown: raise SystemExit('unknown case(s): '+', '.join(unknown))
    selected=[(cid,desc,fn) for cid,desc,fn in CASES if cid in set(selected_ids)]
    for cid in selected_ids:
        c=fixtures/cid
        if not c.exists(): raise SystemExit(f'missing fixture case: {c}')
    out=HERE/ns.out; out.mkdir(parents=True,exist_ok=True)

    versions=[('4.0.0',HERE/'arcager.py')]
    if not ns.no_v3:
        if not V3.exists(): raise SystemExit(f'missing bundled 3.2.3 baseline: {V3}')
        versions.insert(0,('3.2.3',V3))
    brotli_samples={'02-png-gallery','09-tetris-game','14-secret-7z-and-csv','24-large-mixed'} if ns.brotli_samples else set()
    rows=[]; cap_rows=[]
    total=len(selected)
    for i,(cid,desc,_) in enumerate(selected,1):
        case_dir=fixtures/cid
        source=cases_manifest[cid]['source_bytes']
        print(f'[{i}/{total}] {cid}: {cases_manifest[cid]["file_count"]} fixture files / {source:,} source bytes',file=sys.stderr,flush=True)
        for version,script in versions:
            codecs=['gzip'] + (['brotli'] if ns.v3_brotli and version=='3.2.3' else [])
            for codec in codecs:
                try:
                    r=pack_and_decode(python_cmd,script,version,cid,case_dir,source,codec,verify=(version=='4.0.0' and not ns.skip_roundtrip_check))
                    r['description']=desc; rows.append(r)
                except Exception as exc:
                    rows.append({'case':cid,'description':desc,'version':version,'codec':codec,'error':str(exc)})
                print(f'    {version} {codec}: complete',file=sys.stderr,flush=True)
        if cid in brotli_samples:
            try:
                r=pack_and_decode(python_cmd,HERE/'arcager.py','4.0.0',cid,case_dir,source,'brotli',verify=(not ns.skip_roundtrip_check))
                r['description']=desc; rows.append(r)
            except Exception as exc:
                rows.append({'case':cid,'description':desc,'version':'4.0.0','codec':'brotli','error':str(exc)})
        if ns.capabilities and cid in {'02-png-gallery','04-jpeg-gallery','06-animated-gif','14-secret-7z-and-csv'}:
            for flags in [dict(lossy=True),dict(strip=True),dict(encrypt=True),dict(hidden=True)]:
                try:
                    cr=run_v4_capability_variant(python_cmd,HERE/'arcager.py',cid,case_dir,source,'gzip',**flags)
                    cr['description']=desc; cap_rows.append(cr)
                except Exception as exc:
                    cap_rows.append({'case':cid,'description':desc,**flags,'codec':'gzip','error':str(exc)})

    def _module_version(module_name):
        if not python_info['modules'].get(module_name, False):
            return None
        try:
            pr=subprocess.run([*python_cmd,'-c',f'import {module_name}; print(getattr({module_name}, "__version__", "unknown"))'],capture_output=True,text=True,check=True,timeout=8)
            return pr.stdout.strip()
        except Exception:
            return 'unknown'
    crypto={'available': python_info['modules'].get('cryptography', False), 'interpreter': python_info['executable'], 'version': _module_version('cryptography')}
    brotli_info={'available':python_info['modules'].get('brotli', False),'version':_module_version('brotli')}
    pil_info={'available':python_info['modules'].get('PIL', False),'version':_module_version('PIL')}
    report={'benchmark':'Arcager 24-scenario reproducible benchmark','fixture_manifest':str(fixtures/'manifest.json'),'versions':[x[0] for x in versions],
            'environment':{'driver_python':sys.executable,'python':python_info['executable'],'python_version':python_info['version'],'platform':platform.platform(),'python_command':python_cmd,
                           'node':shutil.which('node'),'cryptography':crypto,'brotli':brotli_info,'pillow':pil_info},
            'rows':rows,'capability_rows':cap_rows,'fixture_cases':selected_ids}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    fields=['case','description','version','codec','source_bytes','source_site_bytes','source_asset_bytes','package_bytes','container_bytes','raw_resource_bytes','payload_bytes','metadata_bytes','z85_bytes','z85_overhead_bytes','shell_bytes','final_html_delta_pct','stream_reduction_pct','z85_overhead_pct','container_metadata_pct','shell_overhead_pct','compression_ratio','encode_seconds','decode_seconds','encode_MB_s','decode_MB_s','logical','physical','duplicates','streams','asset_roundtrip_ok','logical_checked','logical_exact','logical_transformed','error']
    with (out/'results.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in rows: w.writerow({k:r.get(k) for k in fields})

    lines=['# Arcager 4.0 Solid Update — reproducible benchmark report','',
           f"Benchmark subprocess interpreter: `{python_info['executable']}` · Python {python_info['version']} · {platform.platform()}\nDriver interpreter: `{sys.executable}`",
           f"Optional libraries: cryptography={'available' if crypto.get('available') else 'unavailable'}{(' v'+str(crypto.get('version'))) if crypto.get('version') else ''}; brotli={'available' if brotli_info.get('available') else 'unavailable'}{(' v'+str(brotli_info.get('version'))) if brotli_info.get('version') else ''}; Pillow={'available' if pil_info.get('available') else 'unavailable'}{(' v'+str(pil_info.get('version'))) if pil_info.get('version') else ''}",
           '',
           'This report was generated from the **shipped fixture files** in `benchmarks/fixtures/`. No example is inferred from prose.', '',
           'The headline package ratio is **source bytes ÷ final HTML bytes**. It is intentionally separated from the accounting below so an already-compressed PNG workload cannot be mistaken for failed resource compression.', '',
           '| Case | Version | Codec | Source MiB | Final HTML MiB | Ratio | Final HTML Δ | Raw resources MiB | Stream payload MiB | Z85 overhead MiB | Shell KB | Encode MiB/s | Decode MiB/s | Exact assets |',
           '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in rows:
        if 'error' in r:
            lines.append(f"| {r['case']} | {r['version']} | {r['codec']} | ERROR | | | | | | | | | | |")
        else:
            exact='yes' if r.get('asset_roundtrip_ok') else ('no' if r.get('asset_roundtrip_ok') is False else '—')
            lines.append(f"| {r['case']} | {r['version']} | {r['codec']} | {fmt_mb(r['source_bytes']):.2f} | {fmt_mb(r['package_bytes']):.2f} | {r['compression_ratio']:.2f}× | {r['final_html_delta_pct']:+.1f}% | {fmt_mb(r.get('raw_resource_bytes',0)):.2f} | {fmt_mb(r.get('payload_bytes',0)):.2f} | {fmt_mb(r.get('z85_overhead_bytes',0)):.2f} | {r.get('shell_bytes',0)/1024:.1f} | {r.get('encode_MB_s',0):.2f} | {r.get('decode_MB_s',0):.2f} | {exact} |")
    lines += ['', '## How to interpret the accounting', '',
               '- **Raw resources** are the bytes after representation selection/dedup and before stream compression.',
               '- **Stream payload** is what the selected stored/compressed streams actually occupy.',
               '- **Z85 overhead** is the binary-container-to-HTML text representation cost.',
               '- **Shell** is the remaining HTML wrapper/runtime cost.',
               '- A compressed stream is accepted only when its stored payload is smaller than its raw stream size; the runner fails loudly if this invariant is violated.',
               '- `Exact assets = yes` means all checked logical resources matched exactly or matched after undoing Arcager\'s documented `arcager-res:N` reference rewrite. The report also separates exact and transformed matches.', '',
               'Run `python benchmarks/examples.py --verify` to verify the fixture corpus, then rerun this benchmark to produce a fresh report on your machine.']
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(out/'report.md')

if __name__=='__main__':
    main()
