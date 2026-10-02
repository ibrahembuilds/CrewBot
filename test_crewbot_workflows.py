"""CrewBot team-aware plans, review gates and scoped connection access checks."""
import copy
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import company_dashboard as company
from company_os import CompanyOS
from providers import PROVIDERS
import scout


class CrewBotWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        for name in ('company.json','agent.json'):
            shutil.copy(company.ROOT/('tests/fixtures/company.json' if name=='company.json' else name),self.folder/name)
        employees=scout.load_json(company.ROOT/'tests/fixtures/employees.json')
        for employee in employees:
            employee['capability_role']=employee['id']
            employee['id']='crew-'+employee['id']
        scout.save_json(self.folder/'employees.json',employees)
        self.env=patch.dict(os.environ,{entry[1]:'' for entry in PROVIDERS.values()});self.env.start()
        self.w=company.Workspace(self.folder);CompanyOS(self.w,False)
        self.ops=self.w.operations;self.launched=[]
        self.w.launch=lambda identifier,resume=False:self.launched.append(identifier)

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def test_templates_resolve_current_team_ids_and_never_select_mentor(self):
        templates=self.ops.snapshot()['templates']
        full=next(t for t in templates if t['id']=='lead-to-delivery')
        self.assertTrue(full['available']);self.assertEqual(full['missing_roles'],[])
        self.assertEqual(full['steps'][0]['employee_id'],'crew-director')
        self.assertNotIn('mentor',[s['employee_id'] for s in full['steps']])
        flow=self.ops.create_flow({'template_id':'prospecting','title':'Regional leads','brief':'Use this company profile'})
        self.assertEqual([s['employee_id'] for s in flow['steps']],['crew-growth','crew-offers'])
        self.assertEqual(self.launched,[])

    def test_missing_capabilities_are_explicit_and_unknown_template_rejected(self):
        del self.w.roles['crew-offers'];del self.w.roles['crew-director']
        templates=self.ops.templates()
        prospecting=next(t for t in templates if t['id']=='prospecting')
        self.assertFalse(prospecting['available']);self.assertEqual(prospecting['missing_roles'],['offers'])
        with self.assertRaisesRegex(scout.ScoutError,'offers'):
            self.ops.create_flow({'template_id':'prospecting','title':'Missing hire','brief':'No offers employee'})
        self.assertIn('director',next(t for t in templates if t['id']=='lead-to-delivery')['missing_roles'])
        with self.assertRaisesRegex(scout.ScoutError,'Unknown workflow template'):
            self.ops.create_flow({'template_id':'nonexistent','title':'Bad','brief':'Bad'})
        self.assertEqual(self.w.store['flows'],[])

    def test_custom_team_handoff_waits_for_review_and_shares_verified_results(self):
        flow=self.ops.create_flow({'template_id':'prospecting','title':'Find customers','brief':'Local fixture business'})
        self.ops.start_flow(flow['id']);record=self.ops.find('flows',flow['id'])
        first=self.w.task(record['steps'][0]['task_id'])
        self.assertEqual(first['employee_id'],'crew-growth')
        first.update(status='review',result='Verified fixture research, source https://example.com')
        self.ops.sync_flow(first)
        self.assertEqual(len(self.launched),1)
        self.ops.flow_action({'id':flow['id'],'action':'approve'})
        second=self.w.task(record['steps'][1]['task_id'])
        self.assertEqual(second['employee_id'],'crew-offers')
        self.assertIn('Verified fixture research',second['description'])
        self.assertIn(first['id'],second['description'])
        self.assertEqual(len(self.launched),2)
        self.assertEqual(first['status'],'done');self.assertEqual(record['status'],'running')
        with self.assertRaises(scout.ScoutError):self.ops.flow_action({'id':flow['id'],'action':'approve'})

    def test_autohandoff_is_opt_in_and_never_dispatches_external_proposals(self):
        flow=self.ops.create_flow({'template_id':'prospecting','title':'Internal handoff','brief':'Local fixture only'})
        self.ops.start_flow(flow['id']);record=self.ops.find('flows',flow['id'])
        task=self.w.task(record['steps'][0]['task_id']);task.update(status='review',result='Fixture evidence')
        self.w.os.finished(task);self.ops.sync_flow(task)
        self.assertEqual(task['status'],'review');self.assertEqual(len(self.launched),1)
        self.w.os.settings['auto_handoffs']=True
        self.w.os.finished(task);self.ops.sync_flow(task)
        self.assertEqual(task['status'],'done');self.assertEqual(len(self.launched),2)
        self.w.os.vault.set('slack','fixture-slack-key')
        self.ops.configure_connection({'connection':'slack','enabled':True,'destinations':['C12345678']})
        proposal=self.ops.propose({'connection':'slack','destination':'C12345678','title':'Fixture notice','content':'Fixture only','reason':'Needs review'},task)
        self.assertEqual(self.ops.find('approvals',proposal['approval_id'])['status'],'pending')

    def test_daily_task_limit_pauses_flow_and_can_resume_without_duplicates(self):
        self.w.os.settings['daily_task_limit']=1
        flow=self.ops.create_flow({'template_id':'prospecting','title':'Bounded work','brief':'Fixture only'})
        self.ops.start_flow(flow['id']);record=self.ops.find('flows',flow['id'])
        first=self.w.task(record['steps'][0]['task_id']);first.update(status='review',result='Fixture')
        with self.assertRaisesRegex(scout.ScoutError,'Daily task limit'):
            self.ops.flow_action({'id':flow['id'],'action':'approve'})
        self.assertEqual(record['status'],'paused');self.assertEqual(record['cursor'],1)
        self.assertIsNone(record['steps'][1]['task_id'])
        self.w.os.settings['daily_task_limit']=2
        self.ops.start_flow(flow['id'])
        self.assertEqual(len(self.w.store['tasks']),2)
        self.assertEqual(len(self.launched),2)

    def test_schedule_uses_owner_provider_and_blocks_until_previous_reviewed(self):
        self.w.roles['crew-growth']['provider']='openai'
        self.w.os.vault.set('openai','fixture-openai-key')
        self.assertFalse(self.w.os.can_run());self.assertTrue(self.w.os.can_run('crew-growth'))
        schedule=self.ops.save_schedule({'employee_id':'crew-growth','title':'Owned research','description':'Fixture only','interval_hours':1,'enabled':True})
        self.ops.tick(schedule['next_run']+1);self.assertEqual(len(self.launched),1)
        task=self.w.task(self.launched[0]);self.assertEqual(task['schedule_id'],schedule['id'])
        task['status']='review';later=self.ops.find('schedules',schedule['id'])['next_run']+1
        self.ops.tick(later);self.assertEqual(len(self.launched),1)
        task['status']='done';self.ops.tick(later);self.assertEqual(len(self.launched),2)
        with self.assertRaises(scout.ScoutError):
            self.ops.save_schedule({'employee_id':'crew-growth','title':'Invalid cadence','description':'Fixture','interval_hours':True,'enabled':True})

    def test_slack_access_check_fails_when_token_valid_but_channel_unreadable(self):
        self.w.os.vault.set('slack','fixture-slack-key')
        self.ops.configure_connection({'connection':'slack','enabled':True,'destinations':['C12345678']})
        requests=[]
        class HTTP:
            def request(inner,provider,method,path,body=None):
                requests.append((provider,method,path))
                if path=='/auth.test':return {'ok':True,'team':'Fixture workspace','user_id':'U12345678'}
                raise scout.ScoutError('Slack: not_in_channel')
        self.ops.http=HTTP()
        with self.assertRaisesRegex(scout.ScoutError,'not_in_channel'):self.ops.check_connection('slack')
        state=next(s for s in self.ops.connection_status() if s['id']=='slack')
        self.assertTrue(state['configured']);self.assertFalse(state['verified'])
        self.assertIn('channel=C12345678&limit=1',requests[1][2])

    def test_github_check_requires_issue_read_access_and_configuration_resets_check(self):
        self.w.os.vault.set('github','fixture-github-key')
        self.ops.configure_connection({'connection':'github','enabled':True,'destinations':['fixture/repository']})
        requests=[]
        class HTTP:
            def request(inner,provider,method,path,body=None):
                requests.append(path)
                if '/issues?' in path:return []
                return {'full_name':'fixture/repository','private':True}
        self.ops.http=HTTP();summary=self.ops.check_connection('github')
        self.assertEqual(summary['checked_repository'],'fixture/repository')
        self.assertIn('issues?state=open&per_page=1',requests[1])
        self.assertTrue(next(s for s in self.ops.connection_status() if s['id']=='github')['verified'])
        self.ops.configure_connection({'connection':'github','enabled':True,'destinations':['fixture/other']})
        state=next(s for s in self.ops.connection_status() if s['id']=='github')
        self.assertFalse(state['verified']);self.assertIsNone(state['summary'])

    def test_changed_credentials_cannot_reuse_an_inflight_read_check(self):
        self.w.os.vault.set('slack','fixture-slack-key')
        self.ops.configure_connection({'connection':'slack','enabled':True,'destinations':['C12345678']})
        app=self.w
        class HTTP:
            def request(inner,provider,method,path,body=None):
                if path=='/auth.test':return {'ok':True,'team':'Fixture'}
                app.os.vault.set('slack','new-fixture-slack-key')
                return {'ok':True,'messages':[]}
        self.ops.http=HTTP()
        with self.assertRaisesRegex(scout.ScoutError,'changed during this check'):
            self.ops.check_connection('slack')
        self.assertFalse(next(s for s in self.ops.connection_status() if s['id']=='slack')['verified'])

    def test_custom_owner_milestone_is_unique_and_delegation_parent_stays_visible(self):
        parent=self.w.create_task({'employee_id':'mentor','title':'Client project','description':'Plan fixture delivery'},start=False)
        child=self.w.create_task({'employee_id':'crew-product','title':'Implementation','description':'Build fixture source'},parent_id=parent['id'],start=False)
        self.assertEqual(child['parent_id'],parent['id'])
        self.assertEqual(child['employee_id'],'crew-product')
        project=self.ops.create_project({'title':'Fixture build','client':'Fixture customer','brief':'Verified runnable source'})
        milestone=self.ops.milestone({'project_id':project['id'],'title':'Tested delivery','employee_id':'crew-product','acceptance':'Owner verifies source'})
        args={'project_id':project['id'],'id':milestone['id'],'action':'assign'}
        first=self.ops.milestone_action(args);again=self.ops.milestone_action(args)
        self.assertEqual(first['id'],again['id']);self.assertEqual(len(self.launched),1)
        task=self.w.task(first['id']);task.update(status='review',result='Source saved; not executed')
        self.ops.sync_flow(task)
        self.assertEqual(self.ops.find('projects',project['id'])['milestones'][0]['status'],'review')
        self.w.action({'task_id':task['id'],'action':'complete'})
        self.assertEqual(self.ops.find('projects',project['id'])['milestones'][0]['status'],'completed')
        self.assertEqual(self.ops.find('projects',project['id'])['status'],'planned')


if __name__=='__main__':unittest.main()
