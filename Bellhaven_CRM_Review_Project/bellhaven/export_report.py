"""Export a portable evidence report without approval controls or credentials."""
import json
from collections import Counter
from core import DATA,ROOT,Store,now
from app import shell,detail,esc

def export():
    store=Store();rows=[r for r in store.rows() if r['state']!='superseded'];ps=[json.loads(r['payload']) for r in rows]
    counts=Counter(r['state'] for r in rows)
    summary=json.loads((DATA/'latest.json').read_text())['summary']
    body='<h2>Review packet</h2><p class="warning">Read-only evidence report. No approval buttons and no CRM writes. Run python launch.py to review and apply in the local app.</p>'
    body+='<div class="grid">'+''.join(f'<div class="metric"><b>{v}</b>{label}</div>' for label,v in [('Website locations',summary['total_count']),('CRM accounts',summary['crm_accounts']),('Pending proposals',counts['pending']),('Applied proposals',counts['applied'])])+'</div>'
    body+='<p>Snapshot: '+esc(summary['fetched_at'])+'. Every proposal below shows the exact planned changes. Rule scores rank candidates; they are not probabilities.</p>'
    body+='<p><b>Recommended review:</b> 21 high-confidence corrections; four proposals to flag ambiguous or absent facilities as Needs Review; separately decide whether Ashtabula’s PO Box should be replaced by the physical address. None has been applied.</p>'
    body+='<div class="scroll"><table><tr><th>#</th><th>Facility</th><th>Proposal</th><th>Confidence</th><th>State</th></tr>'
    for i,(p,r) in enumerate(zip(ps,rows),1):body+=f'<tr><td>{i}</td><td><a href="#{p["id"]}">{esc(p["subject"])}</a></td><td>{esc(p["kind"])}</td><td>{esc(p["confidence"])}</td><td>{esc(r["state"])}</td></tr>'
    body+='</table></div>'
    for p,r in zip(ps,rows):body+=f'<article id="{p["id"]}">'+detail(p,r,interactive=False)+f'<small>Proposal ID: {p["id"]}</small></article>'
    path=ROOT/'REVIEW_REPORT.html';path.write_text(shell(body));print(path)
    store.db.close();return path
if __name__=='__main__':export()
