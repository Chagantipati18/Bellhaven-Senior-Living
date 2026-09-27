import copy
import json
from pathlib import Path
import tempfile
import unittest
from core import *

class FakeCRM:
    def __init__(self, accounts):self.accounts=copy.deepcopy(accounts);self.writes=[];self.fail_after_create=False;self.fail_patch=False
    def all(self,entity):return copy.deepcopy(self.accounts)
    def account(self,aid):return copy.deepcopy(next(a for a in self.accounts if a['account_id']==aid))
    def request(self,path,method='GET',body=None):
        self.writes.append((method,path,copy.deepcopy(body)))
        if method=='POST':
            a={k:'' for k in FIELDS};a.update(lifetime_revenue=0,outstanding_ar=0,account_id='new'+str(len(self.accounts)));a.update(body);self.accounts.append(a)
            if self.fail_after_create:self.fail_after_create=False;raise TimeoutError('response lost')
            return copy.deepcopy(a)
        if method=='PATCH':
            if self.fail_patch:self.fail_patch=False;raise TimeoutError('patch interrupted')
            a=next(a for a in self.accounts if a['account_id']==path.split('/')[-1]);a.update(body);return copy.deepcopy(a)
        raise AssertionError(method)

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def fetch(url):
            p=url.removeprefix(BASE);f={'/':'home.html','/about':'about.html','/communities':'directory.html','/communities?page=1':'directory.html'}.get(p,p.split('/')[-1].replace('?','_')+'.html')
            return (ROOT/'data'/f).read_text()
        cls.locs,cls.pages,cls.meta=scrape(fetch)
        cls.accounts=json.loads((ROOT/'data/accounts.json').read_text())['data']
        cls.contacts=json.loads((ROOT/'data/contacts.json').read_text())['data']
        cls.ps,cls.matches,cls.parent=reconcile(cls.locs,cls.accounts,cls.contacts)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'test.db');self.crm=FakeCRM(self.accounts);self.s.sync(self.ps,{})
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def chosen(self,name):return next(p for p in self.ps if p['subject']==name)
    def approve(self,p):self.s.decide(p['id'],'approve','Test reviewer','Synthetic test only');execute(self.s,p['id'],self.crm)
    def test_scrape_complete_and_homepage_extra(self):
        self.assertEqual(len(self.locs),35);self.assertEqual(self.meta['directory_count'],34)
        l=next(l for l in self.locs if 'Findlay' in l['name']);self.assertEqual(len(l['care_offerings']),2)
    def test_address_normalization(self):
        self.assertEqual(norm('1250 Northwest Franklin Street'),norm('1250 NW Franklin St'))
        self.assertNotEqual(norm('118 Union Square Dr'),norm('240 Market St'))
    def test_same_name_different_geography_not_matched(self):
        self.assertEqual(self.chosen('Amberly Manor')['kind'],'New account')
        self.assertEqual(self.chosen('Bellhaven at Union Square')['kind'],'New account')
    def test_duplicate_survivor_uses_admin(self):
        p=self.chosen('Bellhaven of Owosso');self.assertEqual(p['steps'][0]['body']['duplicate_of_account'],'001EGU7BMJ942ZTRE6')
    def test_ambiguous_and_absent_do_not_reparent(self):
        for p in self.ps:
            if p['kind'] in ['No longer listed','Ambiguous match']:
                for s in p['steps']:self.assertEqual(set(s['body']),{'status','note'})
    def test_without_approval_no_write(self):
        with self.assertRaises(ValueError):execute(self.s,self.ps[0]['id'],self.crm)
        self.assertEqual(self.crm.writes,[])
    def test_rejection_survives_second_run(self):
        p=self.ps[0];self.s.decide(p['id'],'reject','Reviewer','Do not change');self.s.sync(self.ps,{})
        self.assertEqual(self.s.get(p['id'])['state'],'rejected')
        with self.assertRaises(ValueError):execute(self.s,p['id'],self.crm)
    def test_second_run_suppresses_all_decided_items(self):
        rejected,applied=self.ps[:2]
        self.s.decide(rejected['id'],'reject','Reviewer','Do not change')
        self.approve(applied)
        stats=self.s.sync(self.ps,{})
        self.assertEqual(self.s.get(rejected['id'])['state'],'rejected')
        self.assertEqual(self.s.get(applied['id'])['state'],'applied')
        self.assertEqual(stats['new_proposal_count'],0)
        self.assertEqual(stats['suppressed_decided_count'],2)
        self.assertEqual(sum(r['state']=='pending' for r in self.s.rows()),len(self.ps)-2)
    def test_chow_preserves_old_account(self):
        p=self.chosen('Bellhaven of Tiffin');aid=p['steps'][1]['account_id'];before=self.crm.account(aid)
        self.approve(p);after=self.crm.account(aid);newid=after.pop('chow_current_account');before.pop('chow_current_account')
        self.assertEqual(after,before);self.assertEqual(self.crm.account(newid)['parent_id'],self.parent)
        self.assertEqual(self.crm.account(newid)['outstanding_ar'],0)
    def test_sop_combinations(self):
        self.assertTrue(protected(dict(lifetime_revenue=1,outstanding_ar=1)))
        for r,a in [(0,1),(1,0),(0,0)]:self.assertFalse(protected(dict(lifetime_revenue=r,outstanding_ar=a)))
        with self.assertRaises(KeyError):protected({})
    def test_direct_parent_move_checks_revenue(self):
        a=next(a for a in self.accounts if 'Tiffin' in a['name']);p=proposal('Unsafe','Test','',None,[],[patch_step(a,dict(parent_id=self.parent))]);self.s.sync([p],{});self.s.decide(p['id'],'approve','test','')
        with self.assertRaises(RuntimeError):execute(self.s,p['id'],self.crm)
        self.assertEqual(self.crm.writes,[])
    def test_stale_record_prevents_write(self):
        p=self.chosen('Bellhaven Crossings of Lima');aid=p['steps'][0]['account_id'];next(a for a in self.crm.accounts if a['account_id']==aid)['outstanding_ar']=500
        self.s.decide(p['id'],'approve','test','')
        with self.assertRaises(RuntimeError):execute(self.s,p['id'],self.crm)
        self.assertEqual(self.crm.writes,[])
    def test_chow_partial_failure_resumes(self):
        p=self.chosen('Bellhaven of Tiffin');self.crm.fail_patch=True;self.s.decide(p['id'],'approve','test','')
        with self.assertRaises(TimeoutError):execute(self.s,p['id'],self.crm)
        execute(self.s,p['id'],self.crm)
        self.assertEqual(sum(m=='POST' for m,_,_ in self.crm.writes),1);self.assertEqual(self.s.get(p['id'])['state'],'applied')
    def test_lost_create_response_recovers_without_second_post(self):
        p=self.chosen('Amberly Manor');self.crm.fail_after_create=True;self.s.decide(p['id'],'approve','test','')
        with self.assertRaises(TimeoutError):execute(self.s,p['id'],self.crm)
        execute(self.s,p['id'],self.crm)
        self.assertEqual(sum(m=='POST' for m,_,_ in self.crm.writes),1)
    def test_full_approved_simulation_second_run_clean(self):
        for p in self.ps:self.approve(p)
        ps,matched,parent=reconcile(self.locs,self.crm.accounts,self.contacts)
        self.assertEqual(ps,[])
        count=len(self.crm.writes)
        for p in self.ps:execute(self.s,p['id'],self.crm)
        self.assertEqual(len(self.crm.writes),count)
    def test_failing_crawl_never_proposes_absence(self):
        with self.assertRaises(RuntimeError):scrape(lambda url:'<html><a href="/communities">Communities</a></html>')

if __name__=='__main__':unittest.main(verbosity=2)

