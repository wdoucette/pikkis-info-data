#!/usr/bin/env python3
"""Read-only BlueStacks capture, local decoding, validation, and reviewed publication.

check --launch: check installed catalogue and report changes (exit 10 if attention needed).
build --snapshot PATH --ready: operator confirms game update completed; build a candidate.
publish --candidate PATH --reviewed: copy verified candidate into data/; never commits/pushes.
"""
import argparse, hashlib, json, os, re, shutil, shlex, subprocess, tarfile, time, zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT=Path(__file__).resolve().parent.parent
STATE=ROOT/'.emulator'
PACKAGE='com.elex.nikkigp'
ADB=Path(r'C:\Program Files\BlueStacks_nxt\HD-Adb.exe')
PLAYER=Path(r'C:\Program Files\BlueStacks_nxt\HD-Player.exe')
STATIC=('clothes_data.lua','clothes_merge_data.lua','clothes_evolution_data.lua',
        'clothes_convert_data.lua','clothes_resolve_data.lua','clothes_genre_data.lua')

def digest(data):return hashlib.sha256(data).hexdigest()
def read_json(path):return json.loads(Path(path).read_text('utf-8'))
def write_json(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n','utf-8');os.replace(tmp,path)
def selected(name):
    p=PurePosixPath(name)
    return name in STATIC or (len(p.parts)==2 and p.parts[0]=='clothes_data' and re.fullmatch(r'clothes_data_\d+\.lua',p.name))
def run(cmd,**kwargs):return subprocess.run([str(x) for x in cmd],check=True,timeout=180,**kwargs)
class Device:
    def __init__(self,adb,serial):self.adb=adb;self.serial=serial
    def command(self,*args,**kwargs):return run([self.adb,'-s',self.serial,*args],**kwargs)
    def shell(self,command,root=False):
        args=['shell',f'/system/xbin/bstk/su -c {shlex.quote(command)}'] if root else ['shell',command]
        return self.command(*args,stdout=subprocess.PIPE).stdout.decode('utf-8').strip()
    def launch(self):
        if not PLAYER.is_file():raise ValueError('BlueStacks player not found')
        subprocess.Popen([str(PLAYER),'--instance','Pie64','--cmd','launchApp','--package',PACKAGE],
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(30):
            try:
                run([self.adb,'connect',self.serial],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                if self.shell('getprop sys.boot_completed')=='1':
                    time.sleep(20) # allow app startup; completion is still confirmed by the operator
                    return
            except (subprocess.SubprocessError,OSError):pass
            time.sleep(2)
        raise ValueError('Emulator did not boot within one minute')
    def patch_dir(self):
        paths=self.shell(f'ls -d /data/user/0/{PACKAGE}/files/patch/*/lua/staticdata',True).splitlines()
        if len(paths)!=1 or not re.fullmatch(r'/data/user/0/com\.elex\.nikkigp/files/patch/[0-9.]+/lua/staticdata',paths[0]):
            raise ValueError('Unknown patch layout; review active patch selection')
        return paths[0]
    def fingerprint(self,patch):
        apks=self.shell(f'pm path {PACKAGE}').splitlines()
        paths=[line.removeprefix('package:') for line in apks]
        if not paths or any(not re.fullmatch(r'/data/app/[A-Za-z0-9_~=/+.-]+\.apk',p) for p in paths):raise ValueError('Unexpected APK paths')
        selection=' '.join(f'{patch}/{n}' for n in STATIC)+f' {patch}/clothes_data/*.lua'
        # Some patch tables may be absent: the APK is the base. Hash only files present.
        raw=self.shell(f'for f in {selection}; do if [ -f "$f" ]; then sha256sum "$f"; fi; done',True)
        apk_hashes=self.shell('sha256sum '+' '.join(paths),True)
        canonical=[]
        for line in (raw+'\n'+apk_hashes).splitlines():
            sha,path=line.split(None,1)
            if not re.fullmatch(r'[0-9a-f]{64}',sha):raise ValueError('Invalid hash response')
            canonical.append((path.replace(patch+'/','patch/') if path.startswith(patch) else 'apk/'+PurePosixPath(path).name,sha))
        return digest(json.dumps(sorted(canonical)).encode()),paths
    def capture(self):
        if self.shell('id',True).find('uid=0')<0:raise ValueError('Root unavailable')
        patch=self.patch_dir();before,apks=self.fingerprint(patch)
        time.sleep(10)
        if self.fingerprint(patch)[0]!=before:raise ValueError('Assets are changing; wait for the update to finish')
        previous=STATE/'status.json'
        if previous.is_file():
            cached=Path(read_json(previous)['snapshot'])
            if cached.is_dir() and cached.is_relative_to(STATE/'snapshots'):
                saved=verify_snapshot(cached)
                if saved.get('device_hash')==before:return cached,saved
        dest=STATE/'snapshots'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        (dest/'tables').mkdir(parents=True)
        for path in apks:
            local=dest/PurePosixPath(path).name
            self.command('pull',path,str(local),stdout=subprocess.DEVNULL)
            with zipfile.ZipFile(local) as z:
                for name in z.namelist():
                    rel=name.removeprefix('assets/lua/staticdata/')
                    if name.startswith('assets/lua/staticdata/') and selected(rel):
                        target=dest/'tables'/rel;target.parent.mkdir(exist_ok=True,parents=True);target.write_bytes(z.read(name))
                    if name=='lib/arm64-v8a/libnnchanneleu.so':(dest/'libnnchanneleu.so').write_bytes(z.read(name))
            local.unlink() # Keep only selected game data; no app/account backup.
        with (dest/'patch.tar').open('wb') as stream:
            self.command('exec-out',f'/system/xbin/bstk/su -c {shlex.quote(f"cd {patch} && tar cf - .")}',stdout=stream)
        with tarfile.open(dest/'patch.tar') as archive:
            for member in archive:
                name=member.name.removeprefix('./')
                if selected(name) and member.isfile():
                    target=dest/'tables'/name;target.parent.mkdir(exist_ok=True,parents=True)
                    with archive.extractfile(member) as stream:target.write_bytes(stream.read())
        (dest/'patch.tar').unlink()
        after,_=self.fingerprint(patch)
        if before!=after:raise ValueError('Game changed during capture; discard this snapshot and retry')
        manifest=create_manifest(dest,before)
        write_json(dest/'manifest.json',manifest)
        return dest,manifest

def create_manifest(snapshot,device_hash=None):
    snapshot=Path(snapshot)
    files={p.relative_to(snapshot).as_posix():digest(p.read_bytes()) for p in sorted((snapshot/'tables').rglob('*.lua'))}
    files['libnnchanneleu.so']=digest((snapshot/'libnnchanneleu.so').read_bytes())
    if any('tables/'+n not in files for n in STATIC):raise ValueError('Missing required base tables')
    return dict(schema=1,package=PACKAGE,captured=datetime.now(timezone.utc).isoformat(),files=files,
                catalogue_hash=digest(json.dumps(files,sort_keys=True).encode()),device_hash=device_hash)

def verify_snapshot(snapshot):
    manifest=read_json(snapshot/'manifest.json');actual=create_manifest(snapshot)
    if actual['files']!=manifest['files'] or actual['catalogue_hash']!=manifest['catalogue_hash']:raise ValueError('Snapshot integrity mismatch')
    return manifest

def build_candidate(snapshot,ready,overrides=None):
    if not ready:raise ValueError('Finish the game update first, then pass --ready. Stable files alone do not prove update completion.')
    from emulator.decoder import Decoder
    from emulator.catalogue import load_tables,build
    from update_data import resolve_overrides
    snapshot=Path(snapshot).resolve();manifest=verify_snapshot(snapshot)
    decoder=Decoder(snapshot/'libnnchanneleu.so');decoded=snapshot/'decoded'
    for name in manifest['files']:
        if not name.startswith('tables/'):continue
        target=decoded/name.removeprefix('tables/');target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(decoder.decode((snapshot/name).read_bytes()))
    baseline_path=ROOT/'data/pikkis-info.min.json';map_path=ROOT/'data/game-id-map.json'
    baseline=read_json(baseline_path);registry=read_json(map_path)
    # Keep override refresh inside the candidate, not the tracked data directory.
    if overrides is None:
        import update_data
        old_data=update_data.DATA;update_data.DATA=snapshot/'override-cache'
        update_data.DATA.mkdir(exist_ok=True)
        if (ROOT/'data/overrides.csv').exists():shutil.copyfile(ROOT/'data/overrides.csv',update_data.DATA/'overrides.csv')
        try:ov=resolve_overrides(None)
        finally:update_data.DATA=old_data
    else:ov=Path(overrides)
    bundle,report=build(load_tables(decoded),registry,baseline,ov)
    meta=bundle['meta'];meta.pop('nikkicalc_version',None);meta.pop('nikkicalc_updated',None)
    meta.update(source='local-emulator',catalogue_hash=manifest['catalogue_hash'],game_data_captured=manifest['captured'],
        decoder_library_sha256=manifest['files']['libnnchanneleu.so'],
        credit="Item data extracted from the local Love Nikki Global installation. Game content belongs to Elex/Papergames. Editorial overrides by Pikki's Info. Legacy site IDs retained for wardrobe compatibility.",
        counts='rd: direct recipe/evolution consumption; rdc: includes recolours; rr/rrc: existing recursive planning convention. fut: hidden or unavailable dependencies exist. These totals are not a personalised safe-to-decompose verdict.',
        overrides={'source':'Overrides CSV','sha256':digest(ov.read_bytes())} if ov else None)
    out=snapshot/'candidate';out.mkdir(exist_ok=True)
    # Content comparisons exclude acquisition timestamps. Identical content produces no tracked diff.
    semantic=lambda b:dict(items=b['items'],meta={k:v for k,v in b['meta'].items() if k not in ('generated','game_data_captured')})
    if semantic(bundle)==semantic(baseline):bundle=baseline
    write_json(out/'pikkis-info.min.json',bundle)
    # Compact production file to preserve current site's small download.
    (out/'pikkis-info.min.json').write_text(json.dumps(bundle,separators=(',',':'),ensure_ascii=False)+'\n','utf-8')
    write_json(out/'game-id-map.json',registry)
    if ov:shutil.copyfile(ov,out/'overrides.csv')
    write_json(out/'version.json',dict(source='local-emulator',item_count=len(bundle['items']),
        catalogue_hash=meta['catalogue_hash'],generated=bundle['meta']['generated'],game_data_captured=bundle['meta']['game_data_captured'],bundle='data/pikkis-info.min.json'))
    report.update(baseline_sha256=digest(baseline_path.read_bytes()),id_map_sha256=digest(map_path.read_bytes()),
        update_confirmed=True,snapshot_hash=manifest['catalogue_hash'],
        output_hashes={p.name:digest(p.read_bytes()) for p in out.iterdir() if p.is_file() and p.name!='report.json'})
    write_json(out/'report.json',report);print(json.dumps(report,indent=2));print('Candidate:',out)
    return out

def publish(candidate,reviewed):
    if not reviewed:raise ValueError('Review candidate/report.json first, then pass --reviewed')
    candidate=Path(candidate).resolve();report=read_json(candidate/'report.json')
    for name,key in [('pikkis-info.min.json','baseline_sha256'),('game-id-map.json','id_map_sha256')]:
        if digest((ROOT/'data'/name).read_bytes())!=report[key]:raise ValueError('Repository changed since build; rebuild candidate')
    allow={'pikkis-info.min.json','game-id-map.json','overrides.csv','version.json'}
    if set(report['output_hashes'])-allow:raise ValueError('Unexpected candidate outputs')
    for name,sha in report['output_hashes'].items():
        if digest((candidate/name).read_bytes())!=sha:raise ValueError('Candidate changed after validation')
    branch=run(['git','-C',ROOT,'branch','--show-current'],stdout=subprocess.PIPE).stdout.decode().strip()
    if not branch or branch in ('main','master'):raise ValueError('Use a review branch, not main')
    for name in report['output_hashes']:
        target=ROOT/'data'/name;tmp=target.with_suffix('.pending');shutil.copyfile(candidate/name,tmp);os.replace(tmp,target)
    print('Copied verified candidate to review branch. Review git diff, then commit and push explicitly.')

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    check=sub.add_parser('check');check.add_argument('--launch',action='store_true');check.add_argument('--adb',default=str(ADB));check.add_argument('--serial',default='127.0.0.1:5555')
    build=sub.add_parser('build');build.add_argument('--snapshot',required=True);build.add_argument('--ready',action='store_true');build.add_argument('--overrides')
    pub=sub.add_parser('publish');pub.add_argument('--candidate',required=True);pub.add_argument('--reviewed',action='store_true')
    args=parser.parse_args();STATE.mkdir(exist_ok=True)
    # Exclusive process lock; stale locks are deliberately left for operator review after crashes.
    lock=STATE/'update.lock'
    with lock.open('x') as handle:handle.write(str(os.getpid()))
    try:
        if args.command=='build':build_candidate(args.snapshot,args.ready,args.overrides)
        elif args.command=='publish':publish(args.candidate,args.reviewed)
        else:
            device=Device(args.adb,args.serial)
            if args.launch:device.launch()
            snapshot,manifest=device.capture();baseline=read_json(ROOT/'data/pikkis-info.min.json')
            changed=baseline.get('meta',{}).get('catalogue_hash')!=manifest['catalogue_hash']
            status=dict(changed=changed,snapshot=str(snapshot),catalogue_hash=manifest['catalogue_hash'],
                message='Check game update completion, then build with --ready.' if changed else 'Installed catalogue is unchanged; no rebuild.')
            write_json(STATE/'status.json',status);print(json.dumps(status,indent=2))
            if changed:raise SystemExit(10)
    finally:lock.unlink()
if __name__=='__main__':main()
