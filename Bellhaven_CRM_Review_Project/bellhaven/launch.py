"""Cross-platform launcher: enter token privately, optionally refresh, then review."""
import getpass
import os
from http.server import HTTPServer
from core import DATA
from pipeline import run
from app import Handler
if __name__=='__main__':
    if not os.environ.get('BELLHAVEN_API_TOKEN'):
        os.environ['BELLHAVEN_API_TOKEN']=getpass.getpass('CRM token (hidden; not saved): ').strip()
    if not os.environ['BELLHAVEN_API_TOKEN']:raise SystemExit('A token is required. For read-only browsing use python app.py.')
    answer=input('Fetch a fresh website/CRM scan first? [Y/n] ').strip().lower()
    if answer!='n':run()
    print('Open http://127.0.0.1:8765 in your browser. Ctrl+C stops the app.',flush=True)
    HTTPServer(('127.0.0.1',8765),Handler).serve_forever()
