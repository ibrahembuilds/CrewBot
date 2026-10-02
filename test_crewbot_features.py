"""CrewBot first-run, conversational setup and company-scoped appearance checks."""
import base64
import copy
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import company_dashboard as dashboard
from company_os import OSHub, CompanyOS, BRANDING_DEFAULTS
from providers import PROVIDERS
import scout


class CrewBotFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        for name in ('company.json','employees.json','agent.json'):
            shutil.copy(dashboard.ROOT/name,self.folder/name)
        company=scout.load_json(self.folder/'company.json')
        scout.save_json(self.folder/'company.json',{k:[] if isinstance(v,list) else '' for k,v in company.items()})
        scout.save_json(self.folder/'employees.json',[])
        self.env=patch.dict(os.environ,{**{v[1]:'' for v in PROVIDERS.values()},'OPENAI_PROJECT_ID':''});self.env.start()
        self.hub=OSHub(dashboard.Workspace(self.folder));self.app=self.hub.root.os

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def profile(self):
        return {'name':'Elm Dental','summary':'Dental treatments and preventative care','market':'Families in Manchester','goals':'Reduce missed appointments','tools':'Calendar and email'}

    def proposal(self):
        return {'roles':[{'id':'patient-coordinator','name':'Robin','role':'Patient coordinator','capability_role':'success','instructions':'Prepare appointment reminders for owner review. Use approved clinic information.','why':'Reduce missed appointments by preparing reminders and tracking confirmations.'}],
                'connections':[{'name':'Calendar','purpose':'Read appointment times after the owner grants supported access.'}]}

    def test_root_blank_even_when_environment_key_is_available(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'crewbot-fixture-only-key'}):
            self.assertTrue(self.app.vault.get('openai'))
            self.assertFalse(self.app.snapshot()['onboarding']['complete'])
            self.assertEqual(list(self.hub.root.roles),['mentor'])
            self.assertEqual(self.app.settings['project_id'],'')
            self.assertEqual(self.app.suggest()['roles'],[])
            self.assertEqual(scout.load_json(self.folder/'company.json')['name'],'')
            self.assertEqual(self.hub.root.store['projects'],[])

    def test_incremental_profile_preserves_answers_and_requires_business_context(self):
        first=self.app.update_business_profile({'name':'Elm Dental','summary':'Dental treatments'})
        self.assertFalse(first['complete']);self.assertEqual(first['missing'],['market','goals'])
        second=self.app.update_business_profile({'market':'Families in Manchester','goals':'Reduce missed appointments','tools':''})
        self.assertTrue(second['complete'])
        self.assertEqual(self.app.snapshot()['onboarding']['answers']['name'],'Elm Dental')
        self.assertEqual(scout.load_json(self.folder/'company.json')['summary'],'Dental treatments')
        restarted=dashboard.Workspace(self.folder);CompanyOS(restarted,False)
        self.assertTrue(restarted.os.snapshot()['onboarding']['complete'])
        with self.assertRaises(scout.ScoutError):self.app.update_business_profile({'goals':42})
        self.assertEqual(self.app.snapshot()['onboarding']['answers']['goals'],'Reduce missed appointments')

    def test_mentor_tools_save_proposal_without_creating_employees(self):
        task=self.hub.root.create_task({'employee_id':'mentor','title':'Setup','description':'Set up my dental clinic team'},start=False)
        handlers=self.app.handlers(task)
        with self.assertRaises(scout.ScoutError):handlers['save_team_proposal'](self.proposal())
        handlers['update_business_profile'](self.profile())
        proposal=handlers['save_team_proposal'](self.proposal())
        self.assertEqual(list(self.hub.root.roles),['mentor'])
        self.assertEqual(proposal['status'],'pending')
        self.assertEqual(handlers['save_team_proposal'](self.proposal())['id'],proposal['id'])
        self.assertNotIn('branding',handlers);self.assertNotIn('credential',handlers)
        approved=self.hub.action({'action':'bootstrap_team','data':{}})
        self.assertEqual(approved['employees'][0]['employee_id'],'patient-coordinator')
        self.assertEqual(self.hub.root.roles['patient-coordinator']['name'],'Robin')
        self.assertEqual(self.app.snapshot()['team_proposal']['status'],'approved')
        self.assertEqual(self.app.bootstrap_team()['employees'],[])
        self.assertEqual(list(self.hub.root.roles),['mentor','patient-coordinator'])

    def test_proposal_requires_refresh_when_business_changes(self):
        self.app.onboard(self.profile());self.app.save_team_proposal(self.proposal())
        self.app.update_business_profile({'goals':'Improve patient onboarding'})
        self.assertEqual(self.app.snapshot()['team_proposal']['status'],'outdated')
        with self.assertRaises(scout.ScoutError):self.app.bootstrap_team()
        self.assertEqual(list(self.hub.root.roles),['mentor'])

    def test_invalid_proposal_has_no_partial_state_or_unbounded_tools(self):
        self.app.onboard(self.profile())
        proposal=self.proposal();proposal['roles'].append({**proposal['roles'][0],'id':'mentor'})
        with self.assertRaises(scout.ScoutError):self.app.save_team_proposal(proposal)
        self.assertIsNone(self.app.snapshot()['team_proposal'])
        proposal=self.proposal();proposal['roles'][0]['capability_role']='shell-admin'
        with self.assertRaises(scout.ScoutError):self.app.save_team_proposal(proposal)
        self.assertEqual(list(self.hub.root.roles),['mentor'])

    def test_branding_validates_all_before_saving_and_is_tenant_scoped(self):
        other=self.hub.create({'name':'Maple Studio'})['company_id']
        saved=self.hub.action({'action':'branding','data':{'accent':'#AA33cc','navigation':'#112233','background':'#fefefe','workspace_name':'Elm Crew'}})
        self.assertEqual(saved['branding']['accent'],'#aa33cc')
        self.assertEqual(self.hub.snapshot(other)['operating_system']['branding']['accent'],BRANDING_DEFAULTS['accent'])
        old=copy.deepcopy(self.app.snapshot()['branding'])
        with self.assertRaises(scout.ScoutError):self.app.save_branding({'accent':'#123456','navigation':'url(javascript:alert(1))'})
        self.assertEqual(self.app.snapshot()['branding'],old)
        with self.assertRaises(scout.ScoutError):self.app.save_branding({'logo_url':'https://example.com/external.svg'})
        with self.assertRaises(scout.ScoutError):self.app.save_branding({'workspace_name':'x'*81})

    def test_logo_upload_scope_magic_path_and_removal(self):
        raw=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        uri='data:image/png;base64,'+base64.b64encode(raw).decode()
        self.app.save_branding({'logo_data':uri})
        path=self.app.logo_path();self.assertEqual(path.read_bytes(),raw)
        self.assertEqual(path.parent,self.folder/'branding')
        snapshot=self.hub.snapshot('default')['operating_system']['branding']
        self.assertTrue(snapshot['logo_url'].startswith('/api/os/logo?company=default&v='))
        self.assertNotIn('logo',snapshot)
        other=self.hub.create({'name':'Maple Studio'})['company_id']
        self.assertIsNone(self.hub.get(other).os.logo_path())
        self.app.save_branding({'remove_logo':True});self.assertIsNone(self.app.logo_path())
        self.assertTrue(path.exists(),'Removing a logo preserves its recoverable local file.')
        self.app.w.store['branding']['logo']={'filename':'../../company.json'}
        self.assertIsNone(self.app.logo_path())

    def test_logo_rejects_unsupported_and_mismatched_data(self):
        for uri in ('data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=', 'data:image/png;base64,aW52YWxpZCBpbWFnZQ==','data:image/jpeg;base64,invalid!'):
            with self.subTest(uri=uri[:25]):
                with self.assertRaises(scout.ScoutError):self.app.save_branding({'logo_data':uri})
        oversized='data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'x'*(2*1024*1024)).decode()
        with self.assertRaises(scout.ScoutError):self.app.save_branding({'logo_data':oversized})
        self.assertIsNone(self.app.logo_path())


if __name__=='__main__':unittest.main()
