#!/usr/bin/env python3
"""Optional Playwright browser smoke test for Chromium/Firefox/WebKit.
Run only when playwright and its browsers are installed."""
from __future__ import annotations
import http.server, socketserver, subprocess, sys, tempfile, threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT)); import arcager

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self,*a): pass

def main():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e: raise SystemExit('Playwright is not installed; install it separately to run browser smoke tests.') from e
    with tempfile.TemporaryDirectory() as td:
        td=Path(td); (td/'index.html').write_text('<!doctype html><html><body><h1 id="ok">Browser smoke</h1><img src="pic.svg"></body></html>')
        (td/'pic.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20"/></svg>')
        out=td/'packed.html'
        r=subprocess.run([sys.executable,str(ROOT/'arcager.py'),str(td/'index.html'),'-f','-o',str(out)],cwd=ROOT,capture_output=True,text=True)
        if r.returncode: raise SystemExit(r.stderr or r.stdout)
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self,*a): pass
        import functools
        handler=functools.partial(Quiet,directory=str(td))
        server=socketserver.TCPServer(('127.0.0.1',0),handler); threading.Thread(target=server.serve_forever,daemon=True).start()
        url=f'http://127.0.0.1:{server.server_address[1]}/packed.html'
        failures=[]
        with sync_playwright() as p:
            for name,browser_type in [('chromium',p.chromium),('firefox',p.firefox),('webkit',p.webkit)]:
                try:
                    b=browser_type.launch(); page=b.new_page(); page.goto(url,wait_until='load',timeout=30000); page.wait_for_function("window.arcager && window.arcager.ready",timeout=30000); page.wait_for_selector('#ok',timeout=30000); print(name+': PASS'); b.close()
                except Exception as e: failures.append((name,str(e))); print(name+': FAIL: '+str(e))
        server.shutdown()
        if failures: raise SystemExit(1)

if __name__=='__main__': main()
