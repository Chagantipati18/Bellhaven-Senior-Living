"""Local review application. All CRM writes require an explicit decision."""
import argparse
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import urllib.parse
from core import DATA, Store, CRM, execute, process_lock

CSRF=secrets.token_urlsafe(32)
def esc(x):return html.escape(str(x if x is not None else ''))
STYLE='''*{box-sizing:border-box}body{margin:0;background:#f5f6f8;color:#18282b;font:15px/1.55 system-ui,sans-serif}header{background:#163d35;color:white;padding:25px max(24px,calc((100vw - 1180px)/2))}header h1{margin:0;font-size:29px}main{max-width:1180px;margin:auto;padding:26px 20px}h2{font-size:23px}h3{margin-bottom:8px}.subtitle{opacity:.75}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}.metric,article{background:white;border:1px solid #dce2e4;border-radius:10px;padding:20px;margin-bottom:16px}.metric b{display:block;font-size:27px}.tag{border-radius:20px;padding:4px 11px;background:#e7eee9;font-size:12px;display:inline-block}.pending{background:#fff0cd}.applied{background:#d4f4dd}.error{background:#ffdada}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:10px;border-bottom:1px solid #dde2e3;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{color:#546063}a{color:#176d58}button,.button{border:0;border-radius:6px;padding:11px 17px;background:#196a54;color:white;cursor:pointer;text-decoration:none;display:inline-block;font-size:14px}.reject{background:#6b3540}input,select{padding:10px;border:1px solid #bdc8c8;border-radius:5px;font:inherit;max-width:100%}.toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:20px}.warning{background:#fff0cd;border-left:4px solid #c29120;padding:14px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f4f4;padding:14px;font-size:12px}details{margin-top:15px}small{color:#657374}.scroll{overflow-x:auto}label{display:inline-block;margin:10px 10px 10px 0}.operation{border-left:3px solid #287f69;padding-left:15px;margin:22px 0}footer{padding:20px;color:#596e68}@media(max-width:640px){main{padding:15px}td,th{padding:6px;font-size:12px}}'''
def shell(body):return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Bellhaven | Ownership review</title><style>{STYLE}</style></head><body><header><h1>Bellhaven / Ownership review</h1><div class="subtitle">Evidence first. Reviewer approved. Billing history preserved.</div></header><main>{body}</main></body></html>'
def operations(p):
    out=''
    for s in p['steps']:
        rows=''.join(f'<tr><td>{esc(k)}</td><td>{esc(s.get("before",{}).get(k,"—"))}</td><td>{esc(v)}</td></tr>' for k,v in s['body'].items())
        out+=f'<div class="operation"><h3>{esc(s["method"])} · {esc(s.get("account_id","New account"))}</h3><div class="scroll"><table><thead><tr><th>Field</th><th>Before</th><th>Proposed</th></tr></thead><tbody>{rows}</tbody></table></div></div>'
    return out

def detail(p,row=None,interactive=True):
    body=f'<h2>{esc(p["subject"])}</h2><p><span class="tag">{esc(p["kind"])}</span> <span class="tag">{esc(p["confidence"])} confidence</span></p><p>{esc(p["reason"])}</p>'
    l=p.get('location')
    if l:
        body+=f'<p><b>Website:</b> {esc(l["street"])}, {esc(l["city"])}, {esc(l["state"])} {esc(l["zip"])}<br>{esc(" · ".join(l["care_offerings"]))}<br>Administrator: {esc(l["administrator"])} · Phone: {esc(l["phone"])}<br><a href="{esc(l["source_url"])}" target="_blank" rel="noopener noreferrer">Open source page</a></p>'
    body+='<h3>Candidate evidence</h3><div class="scroll"><table><tr><th>Account</th><th>Address / parent</th><th>Evidence</th><th>Revenue / unpaid AR</th></tr>'
    for c in p['candidates']:
        a=c['account'];body+=f'<tr><td>{esc(a["name"])}<br><small>{esc(a["account_id"])}</small></td><td>{esc(a["billing_street"])}<br>{esc(a.get("parent_name",a["parent_id"]))}</td><td>{esc("; ".join(c["reasons"]))}<br><small>Rule score: {esc(c["score"])} (not a probability)</small></td><td>${float(a["lifetime_revenue"]):,.2f}<br>${float(a["outstanding_ar"]):,.2f}</td></tr>'
    body+='</table></div><h3>Proposed operations</h3>'+operations(p)
    if p['kind']=='CHOW':body+='<p class="warning">The new account ID replaces $created. Only chow_current_account changes on the old account; its parent, billing fields, revenue, AR, status, and note are preserved.</p>'
    if row:
        body+=f'<p>Status: <b>{esc(row["state"])}</b> · Reviewer: {esc(row.get("reviewer") or "Not decided")}<br>{esc(row.get("reason") or "")}</p>'
        if interactive and row['state']=='pending':
            body+=f'<form method="post" action="/decision"><input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="id" value="{esc(p["id"])}"><label>Your name <input name="reviewer" required maxlength="120"></label><label>Decision note <input name="reason" maxlength="1000"></label><p><button name="choice" value="approve">Approve and apply to CRM</button> <button class="reject" name="choice" value="reject">Reject proposal</button></p></form>'
        elif interactive and row['state'] in ['error','approved']:
            body+=f'<p class="warning">This operation needs attention. Review the audit trail. Resume checks committed steps and will not blindly repeat an uncertain creation.</p><form method="post" action="/resume"><input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="id" value="{esc(p["id"])}"><button>Resume already approved operation</button></form>'
    return body

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):pass
    def send_html(self,s,status=200):
        b=s.encode();self.send_response(status);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");self.end_headers();self.wfile.write(b)
    def trusted(self):return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
    def do_GET(self):
        if not self.trusted():return self.send_html('Invalid host',403)
        try:
            path=urllib.parse.urlparse(self.path);query=urllib.parse.parse_qs(path.query);store=Store()
            if path.path=='/proposal':
                row=store.get(query['id'][0]);p=json.loads(row['payload']);body='<a href="/">← Review queue</a>'+detail(p,row)
                events=[dict(r) for r in store.db.execute('SELECT * FROM events WHERE proposal_id=? ORDER BY id',(row['id'],))]
                body+='<details><summary>Audit trail</summary><pre>'+esc(json.dumps(events,indent=2))+'</pre></details>'
            elif path.path=='/':
                rows=store.rows();counts={s:sum(r['state']==s for r in rows) for s in ['pending','applied','rejected','error']}
                body='<div class="grid">'+''.join(f'<div class="metric"><b>{n}</b>{s.title()}</div>' for s,n in counts.items())+'</div>'
                latest=DATA/'latest.json'
                if latest.exists():
                    summary=json.loads(latest.read_text())['summary'];body+=f'<p>Latest scan: {esc(summary["fetched_at"])} · {summary["total_count"]} locations · {summary["crm_accounts"]} CRM accounts · {summary["confident_matches"]} matches needing no changes.</p>'
                state=query.get('state',['pending'])[0]
                body+='<div class="toolbar">'+''.join(f'<a class="button" href="/?state={s}">{s.title()}</a>' for s in ['pending','applied','rejected','error','all'])+'</div>'
                selected=[r for r in rows if state=='all' or r['state']==state]
                if not selected:body+='<article>No proposals in this view.</article>'
                for r in selected:
                    p=json.loads(r['payload']);body+=f'<article><span class="tag {esc(r["state"])}">{esc(r["state"])}</span> <span class="tag">{esc(p["kind"])}</span><h2><a href="/proposal?id={r["id"]}">{esc(p["subject"])}</a></h2><p>{esc(p["reason"])}</p><small>{len(p["steps"])} operation(s) · {esc(p["confidence"])} confidence</small></article>'
                body+='<footer>Daily scans propose only. Your approval records the decision and applies exactly the displayed operations. Keep data/review.sqlite3 between runs.</footer>'
            else:return self.send_html('Not found',404)
            self.send_html(shell(body));store.db.close()
        except Exception as e:self.send_html(shell('<h2>Unable to load</h2><p>'+esc(e)+'</p><a href="/">Return to queue</a>'),400)
    def do_POST(self):
        if not self.trusted():return self.send_html('Invalid host',403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=8192:raise ValueError('Invalid request length')
            f={k:v[0] for k,v in urllib.parse.parse_qs(self.rfile.read(length).decode()).items()}
            if not secrets.compare_digest(f.get('csrf',''),CSRF):return self.send_html('Invalid form token',403)
            if self.path not in ['/decision','/resume']:return self.send_html('Not found',404)
            with process_lock():
                store=Store();pid=f['id']
                if self.path=='/decision':
                    # Check credentials before recording an approval, without making a CRM call.
                    crm=CRM() if f['choice']=='approve' else None
                    store.decide(pid,f['choice'],f['reviewer'],f.get('reason',''))
                    if f['choice']=='approve':execute(store,pid,crm)
                else:execute(store,pid,CRM())
                store.db.close()
            self.send_response(303);self.send_header('Location','/proposal?id='+urllib.parse.quote(pid));self.end_headers()
        except Exception as e:self.send_html(shell('<h2>Action paused</h2><p>'+esc(e)+'</p><a href="/">Return to queue and inspect audit trail</a>'),409)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=8765);a=p.parse_args()
    print(f'Open http://127.0.0.1:{a.port} — Ctrl+C to stop',flush=True)
    HTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
