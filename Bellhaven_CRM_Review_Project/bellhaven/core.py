"""Bellhaven reconciliation: standard-library-only, deterministic, approval gated."""
import concurrent.futures
import contextlib
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://analyst-assessment-production.up.railway.app'
ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('BELLHAVEN_DATA', ROOT / 'data'))
CARE = {'Short-Term Rehabilitation & Nursing': 'Skilled Nursing',
        'Assisted Living': 'Assisted Living', 'Memory Support': 'Memory Care'}
FIELDS = ['name', 'parent_id', 'billing_street', 'billing_city', 'billing_state',
          'billing_zip', 'care_type', 'phone', 'status', 'note', 'lifetime_revenue',
          'outstanding_ar', 'chow_current_account', 'duplicate_of_account']

def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def canonical(obj): return json.dumps(obj, sort_keys=True, separators=(',', ':'))
def digest(obj): return hashlib.sha256(canonical(obj).encode()).hexdigest()
def norm(value):
    s = re.sub(r'[^a-z0-9 ]', ' ', str(value or '').lower())
    terms = {'street':'st','road':'rd','avenue':'ave','lane':'ln','drive':'dr',
             'boulevard':'blvd','north':'n','south':'s','east':'e','west':'w',
             'northwest':'nw','northeast':'ne','southwest':'sw','southeast':'se',
             'pike':'pk','centre':'center','rehabilitation':'rehab'}
    return ' '.join(terms.get(w,w) for w in s.split())
def phone(value): return re.sub(r'\D','',value or '')[-10:]
def snapshot(a): return {k:a.get(k,'') for k in FIELDS}
def protected(a):
    # Missing/malformed financial values must fail closed, not become zero.
    from decimal import Decimal
    return Decimal(str(a['lifetime_revenue'])) > 0 and Decimal(str(a['outstanding_ar'])) > 0

class Node:
    def __init__(self, tag='', attrs=()): self.tag,self.attrs,self.children=tag,dict(attrs),[]
    def text(self): return ' '.join(c.text() if isinstance(c,Node) else c for c in self.children).strip()
    def find(self, tag=None, cls=None):
        out=[]
        for c in self.children:
            if isinstance(c,Node):
                if (not tag or c.tag==tag) and (not cls or cls in c.attrs.get('class','').split()):out.append(c)
                out.extend(c.find(tag,cls))
        return out
class Document(HTMLParser):
    def __init__(self,s):
        super().__init__(convert_charrefs=True);self.root=Node();self.stack=[self.root];self.feed(s)
    def handle_starttag(self,tag,attrs):
        n=Node(tag,attrs);self.stack[-1].children.append(n)
        if tag not in {'br','meta','link','img','input','hr','source','wbr'}:self.stack.append(n)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag:self.stack=self.stack[:i];break
    def handle_data(self,s):self.stack[-1].children.append(s.strip())

class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old=urllib.parse.urlparse(req.full_url);new=urllib.parse.urlparse(newurl)
        if (old.scheme,old.netloc)!=(new.scheme,new.netloc):
            raise RuntimeError('Cross-origin redirect blocked to protect credentials')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def read_url(url, headers=None):
    # Only GETs retry automatically. Never retry writes without reconciliation.
    for attempt in range(3):
        try:
            return urllib.request.build_opener(SameOriginRedirect()).open(urllib.request.Request(url,headers=headers or {}),timeout=40).read().decode()
        except (urllib.error.URLError,TimeoutError):
            if attempt==2:raise
            time.sleep(attempt+1)

class CRM:
    def __init__(self, token=None):
        self.token=token or os.environ.get('BELLHAVEN_API_TOKEN')
        if not self.token:raise ValueError('Set BELLHAVEN_API_TOKEN before accessing the CRM.')
    def request(self,path,method='GET',body=None):
        headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'}
        url=BASE+'/api/v1/'+path
        if method=='GET':return json.loads(read_url(url,headers))
        req=urllib.request.Request(url,data=json.dumps(body).encode(),headers=headers,method=method)
        with urllib.request.build_opener(SameOriginRedirect()).open(req,timeout=40) as r:return json.load(r)
    def all(self,entity):
        records=[];page=1;seen=set();total=None
        while True:
            j=self.request(f'{entity}?page={page}&page_size=100')
            if total is None:total=j['total']
            if total!=j['total']:raise RuntimeError('CRM changed during pagination. Run again.')
            rows=j['data'];key='account_id' if entity=='accounts' else 'contact_id'
            if not rows and len(records)<total:raise RuntimeError('Incomplete CRM pagination')
            for r in rows:
                if r[key] in seen:raise RuntimeError('Repeated CRM page')
                seen.add(r[key]);records.append(r)
            if len(records)==total:return records
            if len(records)>total:raise RuntimeError('CRM count mismatch')
            page+=1
    def account(self,aid):
        j=self.request('accounts/'+urllib.parse.quote(aid,safe=''))
        return j.get('data',j)

def parse_location(url, source):
    d=Document(source).root;hs=d.find('h1');details=d.find('dl','detail')
    if len(hs)!=1 or len(details)!=1:raise ValueError('Missing location detail: '+url)
    labels=details[0].find('dt');values=details[0].find('dd')
    kv={k.text():v for k,v in zip(labels,values)}
    # Street and city boundary comes from the BR, not a greedy address regex.
    parts=[];current=[]
    for n in kv['Address'].children:
        if isinstance(n,Node) and n.tag=='br':parts.append(' '.join(current).strip());current=[]
        else:current.append(n.text() if isinstance(n,Node) else n)
    parts.append(' '.join(current).strip())
    if len(parts)!=2:raise ValueError('Unexpected address format: '+url)
    m=re.fullmatch(r'(.+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)',parts[1])
    if not m:raise ValueError('Invalid city/state/ZIP: '+url)
    offerings=[x.text() for x in kv['Care Offerings'].find(cls='badge')]
    if not offerings or any(x not in CARE for x in offerings):raise ValueError('Unknown care offering: '+url)
    notices=[x.text() for x in d.find(cls='notice')]
    return dict(name=hs[0].text(),street=parts[0],city=m[1],state=m[2],zip=m[3],
                care_offerings=offerings,care_type='; '.join(CARE[x] for x in offerings),
                administrator=kv.get('Administrator',Node()).text(),phone=kv.get('Phone',Node()).text(),
                notices=notices,source_url=url,source_sha256=hashlib.sha256(source.encode()).hexdigest())

def scrape(fetch=read_url):
    pages={};todo=['/','/communities','/about'];locations=set();directory_locations=set();listed=None;expected=None
    while todo:
        path=todo.pop(0)
        if path in pages:continue
        if len(pages)>100:raise RuntimeError('Unexpected pagination loop')
        source=fetch(BASE+path);pages[path]=source;doc=Document(source).root
        if path=='/':
            m=re.search(r'serve\s+(\d+)\s+communities',doc.text());expected=int(m[1]) if m else None
        if path.startswith('/communities'):
            m=re.search(r'(\d+) communities listed',doc.text())
            if m:listed=int(m[1])
        for a in doc.find('a'):
            u=urllib.parse.urlparse(urllib.parse.urljoin(BASE+path,a.attrs.get('href','')))
            if u.netloc!=urllib.parse.urlparse(BASE).netloc:continue
            if u.path.startswith('/communities/'):
                locations.add(u.path)
                if path.startswith('/communities'):directory_locations.add(u.path)
            elif u.path=='/communities':
                p=u.path+('?' + u.query if u.query else '')
                if p not in pages:todo.append(p)
    if listed is None or len(directory_locations)!=listed:raise RuntimeError('Directory completeness check failed')
    if expected is None or len(locations)!=expected:raise RuntimeError('Homepage/directory count mismatch; investigate before proposals')
    def detail(p):return p,fetch(BASE+p)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for p,s in pool.map(detail,sorted(locations)):pages[p]=s
    locs=[parse_location(BASE+p,pages[p]) for p in sorted(locations)]
    if len({(norm(l['street']),l['city'],l['state']) for l in locs})!=len(locs):
        raise RuntimeError('Website repeats an address; manual inspection required')
    return locs,pages,dict(directory_count=listed,total_count=len(locs),fetched_at=now())

def candidates(loc, accounts, contacts, parent):
    out=[]
    for a in accounts:
        if not a.get('billing_street') or a.get('duplicate_of_account') or a.get('chow_current_account'):continue
        city=norm(a['billing_city'])==norm(loc['city']);state=a['billing_state']==loc['state']
        address=norm(a['billing_street'])==norm(loc['street']);name=norm(a['name'])==norm(loc['name'])
        admin=any(c['account_id']==a['account_id'] and c.get('is_active') and norm(c['name'])==norm(loc['administrator']) for c in contacts)
        tel=bool(phone(loc['phone'])) and phone(a.get('phone'))==phone(loc['phone'])
        # City/state is mandatory. Names alone never establish a facility match.
        if not (city and state):continue
        if not (address or name or admin or tel):continue
        reasons=['same city/state'];score=20
        for yes,pts,label in [(address,50,'normalized street matches'),(name,20,'name matches'),(admin,25,'active contact matches website administrator'),(tel,15,'phone matches')]:
            if yes:score+=pts;reasons.append(label)
        if a.get('billing_zip','')[:5]==loc['zip'][:5]:score+=5;reasons.append('ZIP matches')
        if a.get('parent_id')==parent:score+=5;reasons.append('already under Bellhaven')
        out.append(dict(account=a,score=score,reasons=reasons,exact_address=address,admin_match=admin))
    return sorted(out,key=lambda c:(-c['score'],c['account']['account_id']))

def patch_step(a, changes):
    return dict(method='PATCH',account_id=a['account_id'],before=snapshot(a),body=changes)

def append_note(a,text):return ((a.get('note') or '').rstrip()+'\n'+text).strip()
def desired(loc,parent):
    return dict(name=loc['name'],parent_id=parent,billing_street=loc['street'],
                billing_city=loc['city'],billing_state=loc['state'],billing_zip=loc['zip'],
                care_type=loc['care_type'],status='Active')
def delta(a,target):
    d={}
    for k,v in target.items():
        if k in ['billing_street','billing_city'] and norm(a.get(k))==norm(v):continue
        if a.get(k)!=v:d[k]=v
    return d

def proposal(subject,kind,reason,loc,cands,steps,confidence='High'):
    p=dict(subject=subject,kind=kind,reason=reason,location=loc,candidates=cands,steps=steps,confidence=confidence)
    # Exclude fetch timestamps/HTML hashes so cosmetic site edits do not revive decisions.
    semantic_loc={k:v for k,v in (loc or {}).items() if k not in ['source_sha256']}
    p['id']=digest(dict(subject=subject,kind=kind,location=semantic_loc,steps=steps))[:24]
    return p

def reconcile(locs,accounts,contacts):
    parents=[a for a in accounts if a['name']=='Bellhaven Senior Living (Parent Account)' and not a['parent_id']]
    if len(parents)!=1:raise RuntimeError('Expected exactly one Bellhaven parent')
    parent=parents[0]['account_id'];proposals=[];matched=[];used=set()
    for loc in locs:
        cs=candidates(loc,accounts,contacts,parent);target=desired(loc,parent);steps=[]
        if not cs:
            body={**target,'phone':loc['phone'],'note':'New location verified at '+loc['source_url']}
            p=proposal(loc['name'],'New account','No existing account agrees on geography plus address, identity, phone, or administrator.',loc,cs,[dict(method='POST',body=body)])
            proposals.append(p);continue
        top=cs[0];a=top['account'];same=[c for c in cs if c['exact_address']]
        # A site-address collision with tied evidence is not resolved by row order.
        ambiguous=len(cs)>1 and top['score']-cs[1]['score']<15
        if ambiguous or (not top['exact_address'] and not (norm(a['name'])==norm(loc['name']) and (top['admin_match'] or phone(a.get('phone'))==phone(loc['phone'])))):
            reason='Conflicting or insufficient identity evidence. Verify the surviving account before re-parenting or deduplicating.'
            for c in cs:
                x=c['account'];used.add(x['account_id'])
                if x['status']!='Needs Review':steps.append(patch_step(x,{'status':'Needs Review','note':append_note(x,reason+' Website: '+loc['source_url'])}))
            if steps:proposals.append(proposal(loc['name'],'Ambiguous match',reason,loc,cs,steps,'Low'))
            else:matched.append(dict(location=loc,classification='Needs Review',account_id=a['account_id']))
            continue
        used.add(a['account_id']);changes=delta(a,target)
        moving=a.get('parent_id')!=parent
        if moving and protected(a):
            body={**target,'phone':loc['phone'],'note':'CHOW replacement for '+a['account_id']+'. Verified at '+loc['source_url']}
            steps=[dict(method='POST',body=body),patch_step(a,{'chow_current_account':'$created'})]
            kind='CHOW';reason='Revenue history and positive AR: preserve every old field except the CHOW link; create a new account under Bellhaven.'
        else:
            kind='Fix match';reason='Identity supported by '+', '.join(top['reasons'])+'.'
            if changes:
                changes['note']=append_note(a,'Verified against '+loc['source_url']+'. '+reason)
                steps.append(patch_step(a,changes))
        # Duplicates must agree on street/city/state and have a clearly preferred survivor.
        for c in same:
            x=c['account']
            if x['account_id']==a['account_id']:continue
            used.add(x['account_id'])
            if moving and protected(a):continue  # Never attach duplicates to a historical CHOW account.
            if float(x['lifetime_revenue'])>0 or float(x['outstanding_ar'])>0:
                body={'status':'Needs Review','note':append_note(x,'Possible duplicate of '+a['account_id']+'; billing history requires manual investigation. '+loc['source_url'])}
            else:
                body={'status':'Inactive','duplicate_of_account':a['account_id'],
                      'note':append_note(x,'Duplicate of '+a['account_id']+'. Same normalized facility address; survivor has stronger current website identity evidence. '+loc['source_url'])}
            steps.append(patch_step(x,body));kind='Duplicate cleanup' if not changes else 'Fix + duplicates'
        if steps:
            confidence='Medium' if 'billing_street' in changes and not top['exact_address'] else 'High'
            proposals.append(proposal(loc['name'],kind,reason,loc,cs,steps,confidence))
        else:matched.append(dict(location=loc,classification='Confident match',account_id=a['account_id'],reasons=top['reasons']))
    for a in accounts:
        if a['parent_id']!=parent or a['account_id'] in used or a.get('chow_current_account') or a.get('duplicate_of_account'):continue
        reason='Not found after a complete website crawl. Absence alone does not prove closure or a sale; preserve parent and billing fields and investigate.'
        related=[x for x in accounts if x['account_id']!=a['account_id'] and norm(x['billing_street'])==norm(a['billing_street']) and x['billing_city']==a['billing_city'] and x['billing_state']==a['billing_state']]
        if related:reason+=' Other accounts at this address: '+', '.join(x['name']+' ('+x['account_id']+')' for x in related)+'. This is not independent ownership proof.'
        if a['status']!='Needs Review':
            proposals.append(proposal(a['name'],'No longer listed',reason,None,[dict(account=a,score=0,reasons=['Missing from complete current website'])],
                                      [patch_step(a,dict(status='Needs Review',note=append_note(a,reason)))],'Low'))
    return proposals,matched,parent

class Store:
    def __init__(self,path=None):
        self.path=Path(path or DATA/'review.sqlite3');self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,timeout=30);self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,subject TEXT,payload TEXT,state TEXT,created TEXT,decided TEXT,reviewer TEXT,reason TEXT);
        CREATE TABLE IF NOT EXISTS steps(proposal_id TEXT,idx INTEGER,state TEXT,result TEXT,PRIMARY KEY(proposal_id,idx));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at TEXT,proposal_id TEXT,event TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,at TEXT,summary TEXT);
        ''')
    def event(self,pid,event,detail=''):
        self.db.execute('INSERT INTO events(at,proposal_id,event,detail) VALUES(?,?,?,?)',(now(),pid,event,detail));self.db.commit()
    def sync(self,ps,summary):
        ids={p['id'] for p in ps}
        with self.db:
            for row in self.db.execute("SELECT id FROM proposals WHERE state='pending'").fetchall():
                if row['id'] not in ids:self.db.execute("UPDATE proposals SET state='superseded' WHERE id=?",(row['id'],))
            for p in ps:
                self.db.execute('INSERT OR IGNORE INTO proposals VALUES(?,?,?,?,?,?,?,?)',(p['id'],p['subject'],canonical(p),'pending',now(),None,None,None))
            self.db.execute('INSERT INTO runs(at,summary) VALUES(?,?)',(now(),canonical(summary)))
    def rows(self):return [dict(r) for r in self.db.execute('SELECT * FROM proposals ORDER BY created,subject')]
    def get(self,pid):
        r=self.db.execute('SELECT * FROM proposals WHERE id=?',(pid,)).fetchone()
        if not r:raise ValueError('Unknown proposal')
        return dict(r)
    def decide(self,pid,choice,reviewer,reason):
        if choice not in ['approve','reject'] or not reviewer.strip():raise ValueError('Choose a decision and enter your name')
        self.db.execute('BEGIN IMMEDIATE')
        r=self.get(pid)
        if r['state']!='pending':self.db.rollback();raise ValueError('Already decided or superseded')
        self.db.execute('UPDATE proposals SET state=?,decided=?,reviewer=?,reason=? WHERE id=?',
                        ('approved' if choice=='approve' else 'rejected',now(),reviewer,reason,pid));self.db.commit()
        self.event(pid,choice,reviewer+': '+reason)

@contextlib.contextmanager
def process_lock(path=None):
    """OS-released advisory lock, shared by cron and UI, including on Windows."""
    p=Path(path or DATA/'pipeline.lock');p.parent.mkdir(parents=True,exist_ok=True)
    f=open(p,'a+b');f.seek(0);f.write(b'0');f.flush();f.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        f.close();raise RuntimeError('Another pipeline or approval is running. Try again shortly.')
    try:yield
    finally:f.close()

def execute(store,pid,crm):
    """Only an explicit recorded approval can enter this function."""
    row=store.get(pid)
    if row['state']=='applied':return
    if row['state'] not in ['approved','error']:raise ValueError('Explicit approval required')
    p=json.loads(row['payload']);created=None
    try:
        # Check all affected records before starting this multi-step proposal.
        for i,s in enumerate(p['steps']):
            prior=store.db.execute('SELECT * FROM steps WHERE proposal_id=? AND idx=?',(pid,i)).fetchone()
            if s['method']=='PATCH' and not prior:
                live=crm.account(s['account_id'])
                if snapshot(live)!=s['before']:raise RuntimeError('CRM changed since review; run pipeline and review a fresh proposal.')
        for i,s in enumerate(p['steps']):
            prior=store.db.execute('SELECT * FROM steps WHERE proposal_id=? AND idx=?',(pid,i)).fetchone()
            if prior and prior['state']=='done':
                if s['method']=='POST':created=json.loads(prior['result'])['account_id']
                continue
            body=json.loads(canonical(s['body']))
            if body.get('chow_current_account')=='$created':
                if not created:raise RuntimeError('Missing replacement account ID')
                body['chow_current_account']=created
            if s['method']=='POST':
                marker='[bellhaven-reconcile:'+pid+':'+str(i)+']'
                body['note']=(body.get('note','')+' '+marker).strip()
                existing=[a for a in crm.all('accounts') if marker in a.get('note','')]
                if len(existing)>1:raise RuntimeError('Multiple accounts have the creation marker; manual repair required')
                if existing:
                    result=existing[0]
                    if any(result.get(k)!=v for k,v in body.items()):raise RuntimeError('Recovered account differs from approved creation')
                elif prior:
                    # A timed-out POST may have committed. Never send a second POST blindly.
                    raise RuntimeError('Creation outcome uncertain. No automatic retry. Inspect CRM and operation journal.')
                else:
                    # Prevent a newly created competing account since the scrape.
                    conflicts=[a for a in crm.all('accounts') if a.get('parent_id')==body['parent_id'] and norm(a.get('billing_street'))==norm(body['billing_street']) and norm(a.get('billing_city'))==norm(body['billing_city']) and a.get('billing_state')==body['billing_state'] and not a.get('duplicate_of_account') and not a.get('chow_current_account')]
                    if conflicts:raise RuntimeError('A current account already exists at this address. Refresh review.')
                    with store.db:store.db.execute('INSERT INTO steps VALUES(?,?,?,?)',(pid,i,'inflight','{}'))
                    result=crm.request('accounts','POST',body);result=result.get('data',result)
                created=result['account_id']
                live=crm.account(created)
                if any(live.get(k)!=v for k,v in body.items()):raise RuntimeError('CRM creation verification failed')
                result=live
            else:
                live=crm.account(s['account_id'])
                if all(live.get(k)==v for k,v in body.items()):result=live
                else:
                    if snapshot(live)!=s['before']:raise RuntimeError('Concurrent CRM edit; refusing stale write')
                    if 'parent_id' in body and body['parent_id']!=live.get('parent_id') and protected(live):raise RuntimeError('SOP blocks direct re-parent with revenue and AR')
                    if 'chow_current_account' in body and set(body)!= {'chow_current_account'}:raise RuntimeError('CHOW must preserve all other old fields')
                    with store.db:store.db.execute('INSERT OR REPLACE INTO steps VALUES(?,?,?,?)',(pid,i,'inflight','{}'))
                    crm.request('accounts/'+s['account_id'],'PATCH',body)
                    result=crm.account(s['account_id'])
                    if any(result.get(k)!=v for k,v in body.items()):raise RuntimeError('CRM patch verification failed')
                    if 'chow_current_account' in body and any(result.get(k,'')!=v for k,v in s['before'].items() if k!='chow_current_account'):
                        raise RuntimeError('Old CHOW account preservation check failed')
            with store.db:store.db.execute('INSERT OR REPLACE INTO steps VALUES(?,?,?,?)',(pid,i,'done',canonical(result)))
            store.event(pid,'step_verified',str(i))
        with store.db:store.db.execute("UPDATE proposals SET state='applied' WHERE id=?",(pid,))
        store.event(pid,'applied')
    except Exception as e:
        with store.db:store.db.execute("UPDATE proposals SET state='error' WHERE id=?",(pid,))
        store.event(pid,'error',str(e));raise
