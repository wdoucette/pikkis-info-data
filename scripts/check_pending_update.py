"""Read-only resource-version check. Never downloads patches or changes game data."""
import argparse,base64,json,os,re,subprocess,time,urllib.request,xml.etree.ElementTree as ET,zlib
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ADB=Path(r'C:\Program Files\BlueStacks_nxt\HD-Adb.exe')
PLAYER=Path(r'C:\Program Files\BlueStacks_nxt\HD-Player.exe')
CONFIG=Path.home()/'Documents/Codex/Pikki-Runner/version-check-config.json'
HOST='qjnnlive.elexapp.com'

def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+\.\d+\.\d+',value):raise ValueError('Invalid resource version')
    return tuple(map(int,value.split('.')))

def rc4(data,key):
    if not key:raise ValueError('Missing protocol configuration')
    s=list(range(256));j=0
    for i in range(256):
        j=(j+s[i]+key[i%len(key)])%256;s[i],s[j]=s[j],s[i]
    i=j=0;out=bytearray()
    for b in data:
        i=(i+1)%256;j=(j+s[i])%256;s[i],s[j]=s[j],s[i];out.append(b^s[(s[i]+s[j])%256])
    return bytes(out)

def classify(response,installed):
    version(installed)
    if response.get('ret')!=0:raise ValueError('Version service did not return success')
    data=response.get('data',{})
    if data.get('chn',{}).get('id')!=12 or data.get('chn',{}).get('v_notice')!=1:raise ValueError('Unexpected or suppressed update channel')
    patches=data.get('vlist')
    if not isinstance(patches,list):raise ValueError('Missing patch list')
    versions=[p['version'] for p in patches]
    if any(version(v)<=version(installed) for v in versions):raise ValueError('Unexpected patch ordering/version')
    target=max(versions,key=version) if versions else installed
    return dict(status='pending' if patches else 'current',pending_update=bool(patches),installed_resource_version=installed,
                target_resource_version=target,patch_versions=versions,patch_count=len(patches))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Unexpected version-service redirect')

def query(endpoint,installed,config):
    body=f'channel_id=12&resource_type=2&version={installed}&use_https=1&_client_=1&_client_ver_={installed}&_channel_=12&_rtype_=2&_base_ver_={config["app_version"]}&_i18n_=en&_user_type_=0&_seq_=1'
    key=config['http_key'].encode()
    request=urllib.request.Request(f'http://{HOST}:9580/x/3/resource/{endpoint}',data=base64.b64encode(rc4(body.encode(),key)),headers={'Content-Type':'multipart/form-data'},method='POST')
    with urllib.request.build_opener(NoRedirect).open(request,timeout=25) as r:raw=r.read(2_000_001)
    if len(raw)>2_000_000:raise ValueError('Oversized version response')
    try:return json.loads(raw)
    except (ValueError,UnicodeDecodeError):
        compressed=rc4(base64.b64decode(raw,validate=True),key)
        inflater=zlib.decompressobj(47);decoded=inflater.decompress(compressed,2_000_001)
        if len(decoded)>2_000_000 or not inflater.eof:raise ValueError('Invalid or oversized compressed response')
        return json.loads(decoded)

def adb(*args):
    return subprocess.check_output([str(ADB),'-s','127.0.0.1:5555',*args],timeout=30,stderr=subprocess.DEVNULL).decode('utf8')

def installed_version(config):
    # Boot Android if needed, but do not launch Love Nikki before this check.
    for attempt in range(30):
        subprocess.run([str(ADB),'connect','127.0.0.1:5555'],capture_output=True,timeout=10)
        try:
            if adb('shell','getprop sys.boot_completed').strip()=='1':break
        except subprocess.SubprocessError:pass
        if attempt==0:subprocess.Popen([str(PLAYER),'--instance','Pie64'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(2)
    else:raise ValueError('Emulator unavailable')
    package=adb('shell','dumpsys package com.elex.nikkigp')
    match=re.search(r'versionName=([^\s]+)',package)
    if not match or match[1]!=config['app_version']:raise ValueError('App package changed; revalidate version-check protocol configuration')
    # Parse only game_version in memory. Never save/upload preferences or account data.
    xml=adb('shell',"/system/xbin/bstk/su -c 'cat /data/user/0/com.elex.nikkigp/shared_prefs/Cocos2dxPrefsFile.xml'")
    element=next((e for e in ET.fromstring(xml) if e.get('name')=='game_version'),None)
    saved=config['app_version']
    if element is not None:
        saved=json.loads(rc4(base64.b64decode(element.text,validate=True),config['local_key'].encode()).decode())
    return max((saved,config['app_version']),key=version)

def check(config):
    installed=installed_version(config)
    selected=query('selentry',installed,config)
    entry=selected.get('data',{}).get('entry',{})
    if selected.get('ret')!=0 or entry.get('ip')!=HOST or entry.get('port')!=9580 or entry.get('httpsswitch')!=0 or entry.get('status')!=0 or entry.get('hstatus')!=0:
        raise ValueError('Update service routing/status changed; manual review required')
    result=classify(query('entry',installed,config),installed)
    result['app_version']=config['app_version']
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,default=CONFIG);args=parser.parse_args()
    try:
        result=check(json.loads(args.config.read_text('utf8')))
    except Exception as exc:
        # Avoid exposing preferences, protocol constants or server response bodies.
        result=dict(status='unknown',pending_update=None,error_type=type(exc).__name__,message='Version check failed; inspect local configuration, connectivity or app version. No catalogue capture will run.')
    result['checked_at']=datetime.now(timezone.utc).isoformat()
    folder=ROOT/'.emulator';folder.mkdir(exist_ok=True)
    (folder/'server-version-status.json').write_text(json.dumps(result,indent=2)+'\n','utf8')
    print(json.dumps(result,indent=2))
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf8') as f:
            for k in ('status','installed_resource_version','target_resource_version'):f.write(f'{k}={result.get(k,"")}\n')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf8') as f:
            f.write('## Love Nikki server update check\n\n')
            if result['status']=='pending':f.write(f'**Update required:** {result["installed_resource_version"]} → {result["target_resource_version"]} ({result["patch_count"]} patches). Open Love Nikki, confirm any download prompt, wait for completion, then rerun with `update_ready` checked. No catalogue was published.\n')
            elif result['status']=='current':f.write(f'**No pending resource patches** for installed version {result["installed_resource_version"]}. Continuing to catalogue comparison. This does not assert login/server availability.\n')
            else:f.write('**Update status unknown.** Check failed; catalogue capture is blocked.\n')

if __name__=='__main__':main()
