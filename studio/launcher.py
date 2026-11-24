"""Start the local Studio; loopback only, writable data stays under --data-root."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import socket
import threading
import urllib.request
import webbrowser

ROOT=Path(__file__).resolve().parent

def parser():
    result=argparse.ArgumentParser(description='Local content-package studio')
    result.add_argument('--data-root',default=str(ROOT/'data'))
    result.add_argument('--port',type=int,default=8767)
    result.add_argument('--no-browser',action='store_true')
    result.add_argument('--read-only',action='store_true',help='Veritabanını ve yazma uçlarını salt-okunur inceleme modunda aç')
    result.add_argument('--model-mission',choices=['legacy','extended-modernization-2026-09-08','organic-growth-v1'],default='organic-growth-v1')
    return result

def main():
    args=parser().parse_args()
    data=Path(args.data_root).resolve()
    required_drive=os.environ.get('STUDIO_REQUIRE_DRIVE','').upper()  # optional, e.g. "D:"
    if required_drive and data.drive.upper()!=required_drive:
        raise SystemExit(f'Stüdyo verileri yalnız {required_drive} sürücüsünde tutulabilir.')
    if not 1024<=args.port<=65535:
        raise SystemExit('Yerel port geçersiz.')
    # Never attach to or terminate an unrelated process that owns this port.
    with socket.socket() as probe:
        try: probe.bind(('127.0.0.1',args.port))
        except OSError: raise SystemExit(f'{args.port} portu kullanımda; mevcut sürece dokunulmadı.')
    if args.read_only:
        if not (data/'studio.sqlite3').is_file():
            raise SystemExit('Salt-okunur inceleme için mevcut studio.sqlite3 bulunamadı.')
    else:
        data.mkdir(parents=True,exist_ok=True)
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    if not args.read_only:
        os.environ['TEMP']=os.environ['TMP']=str(data)
    url=f'http://127.0.0.1:{args.port}'
    if not args.no_browser:
        def open_when_ready():
            # Bounded health-driven wait. No new permanent watcher.
            for _ in range(40):
                try:
                    with urllib.request.urlopen(url+'/api/health',timeout=.5) as response:
                        if response.status==200:
                            webbrowser.open(url,new=2);return
                except OSError:
                    threading.Event().wait(.25)
        threading.Thread(target=open_when_ready,daemon=True).start()
    try:
        from .app import create_app
        import uvicorn
    except ImportError:
        raise SystemExit('Stüdyo bağımlılığı eksik. Veriler korunuyor. README.md içindeki kurulum adımlarını kontrol edin; otomatik kurulum yapılmadı.') from None
    uvicorn.run(create_app(data/'studio.sqlite3',data/'assets',model_mission=args.model_mission,read_only=args.read_only),host='127.0.0.1',port=args.port)

if __name__=='__main__':main()
