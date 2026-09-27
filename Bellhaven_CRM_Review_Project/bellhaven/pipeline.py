"""Daily read-only scan. This command never writes to the CRM."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from core import BASE,DATA,CRM,Store,process_lock,scrape,reconcile,now,canonical

def run(offline=False):
    DATA.mkdir(parents=True,exist_ok=True)
    with process_lock():
        store=Store()
        if any(r['state'] in ['approved','error'] for r in store.rows()):
            raise RuntimeError('Resolve or resume existing approved/error operations before scanning again.')
        if offline:
            def fetch(url):
                p=url.removeprefix(BASE)
                f={'/':'home.html','/about':'about.html','/communities':'directory.html','/communities?page=1':'directory.html'}.get(p,p.split('/')[-1].replace('?','_')+'.html')
                return (DATA/f).read_text()
            locs,pages,meta=scrape(fetch)
            accounts=json.loads((DATA/'accounts.json').read_text())['data']
            contacts=json.loads((DATA/'contacts.json').read_text())['data']
        else:
            crm=CRM();locs,pages,meta=scrape();accounts=crm.all('accounts');contacts=crm.all('contacts')
        ps,matched,parent=reconcile(locs,accounts,contacts)
        summary={**meta,'mode':'snapshot' if offline else 'live','crm_accounts':len(accounts),'crm_contacts':len(contacts),
                 'classifications':dict(Counter(p['kind'] for p in ps)),
                 'confident_matches':len(matched),'parent_id':parent}
        store.sync(ps,summary)
        stamp=now().replace(':','-');folder=DATA/'runs'/stamp;folder.mkdir(parents=True)
        for name,obj in [('locations',locs),('accounts',accounts),('contacts',contacts),('proposals',ps),('matches',matched),('summary',summary)]:
            (folder/(name+'.json')).write_text(json.dumps(obj,indent=2))
        (folder/'pages.json').write_text(json.dumps(pages,indent=2))
        (DATA/'latest.json').write_text(json.dumps({'folder':str(folder.relative_to(DATA)),'summary':summary},indent=2))
        with (DATA/'locations.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['name','street','city','state','zip','care_offerings','source_url']);w.writeheader()
            for l in locs:w.writerow({k:('; '.join(l[k]) if isinstance(l[k],list) else l[k]) for k in w.fieldnames})
        states=Counter(r['state'] for r in store.rows());summary['queue_states']=dict(states)
        summary['proposal_count']=summary['new_proposal_count']
        print(json.dumps(summary,indent=2));return summary
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--offline',action='store_true',help='Use the included read-only assessment snapshot');args=p.parse_args();run(args.offline)

