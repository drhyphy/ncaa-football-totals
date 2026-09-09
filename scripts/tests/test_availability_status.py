from datetime import datetime, timedelta
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'availability_status.py'
SPEC = importlib.util.spec_from_file_location('availability_status', SCRIPT)
STATUS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATUS)


class AvailabilityStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.started = STATUS.stamp('2026-09-09T10:17:00Z')
        self.now = self.started + timedelta(minutes=5)
        for name in (*STATUS.SOURCES, STATUS.AMENDMENT):
            path = self.root/'model'/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('frozen fixture '+name)
        workflow = self.root/'.github/workflows/availability.yml'
        workflow.parent.mkdir(parents=True)
        workflow.write_bytes((SCRIPT.parents[1]/'.github/workflows/availability.yml').read_bytes())
        for args in [['init','-q'],['add','.'],['-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','Freeze synthetic source']]:
            subprocess.run(['git', *args],cwd=self.root,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.root,text=True).strip()

    def receipt(self, status=200, suffix='', withheld=False):
        raw = ('DO-NOT-PUBLISH player name logo '+suffix).encode()
        digest = hashlib.sha256(raw).hexdigest()
        body = self.root/STATUS.ARCHIVE/'bodies'/(digest+'.body.gz')
        body.parent.mkdir(parents=True,exist_ok=True)
        body.write_bytes(gzip.compress(raw,mtime=0))
        row = {'schema_version':'raw-http-receipt-v1','requested_at':STATUS.iso(self.started+timedelta(seconds=1)),
               'received_at':STATUS.iso(self.started+timedelta(seconds=2)), 'status_code':status,
               'body_path':body.relative_to(self.root/'model').as_posix(),'body_sha256':digest,'body_size_bytes':len(raw),
               'body_withheld':False,'transport_error':None,'request':{'url':'https://example.invalid/DO-NOT-PUBLISH'}}
        if withheld:
            row.update(body_path=None,body_withheld=True,transport_error='credential_echo_withheld')
        path=self.root/STATUS.ARCHIVE/'receipts'/(STATUS.canonical_hash(row)+'.json')
        row['receipt_path']=path.relative_to(self.root/'model').as_posix()
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(row))
        return row

    def manifest(self, run='123', **changes):
        receipt = self.receipt(suffix=run)
        cohort = self.root/STATUS.ARCHIVE/'cohorts'/f'{run}-1.json'
        cohort.parent.mkdir(parents=True,exist_ok=True)
        cohort.write_text(json.dumps({'inventory_receipt':receipt['receipt_path'],'games':[]}))
        data = {'schema_version':STATUS.CAPTURE_SCHEMA,'protocol':STATUS.PROTOCOL,'run_id':run,'run_attempt':'1','trigger':'schedule',
                'capture_started_at':STATUS.iso(self.started),'capture_completed_at':STATUS.iso(self.started+timedelta(minutes=1)),
                'status':'captured','models_fitted':0,'verified_completed_reports':0,
                'source_hashes':{name:hashlib.sha256((self.root/'model'/name).read_bytes()).hexdigest() for name in STATUS.SOURCES},
                'git_commit':self.commit,'workflow_sha256':hashlib.sha256((self.root/'.github/workflows/availability.yml').read_bytes()).hexdigest(),
                'receipts':[receipt['receipt_path']],'current_receipt':receipt['receipt_path'],
                'cohort_path':cohort.relative_to(self.root/'model').as_posix(),'cohort_sha256':hashlib.sha256(cohort.read_bytes()).hexdigest(),
                'reports':[{'state':'pending','source_teams':['DO-NOT-PUBLISH']}],
                'rows':[{'context_verified':True,'pairs':[{'sportsbook':'DraftKings'}],'logo':'DO-NOT-PUBLISH'}],
                'failures':[], 'counts':dict(source_reports=1,pending_reports=1,nonpending_unverified_reports=0,
                matched_games=1,context_verified_games=1,paired_games=1,paired_two_book_games=0,total_requests=1)}
        data.update(changes)
        path = self.root/STATUS.ARCHIVE/'runs'/f"{run}-{data['run_attempt']}.json"
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data))
        return data, path

    def test_exact_window_and_245_nominal_slots(self):
        grid=STATUS.slots()
        self.assertEqual(len(grid),245)
        self.assertEqual(STATUS.iso(grid[0]),'2026-09-09T10:17:00Z')
        self.assertEqual(STATUS.iso(grid[-1]),'2026-09-16T06:47:00Z')
        for t,expected in [(STATUS.START-timedelta(seconds=1),False),(STATUS.START,True),
                           (STATUS.END-timedelta(seconds=1),True),(STATUS.END,False)]:
            self.assertIs(STATUS.decision(self.root,t)['collect'],expected)
        with self.assertRaises(ValueError): STATUS.phase(datetime(2026,9,9))

    def test_ended_status_publishes_once_with_no_capture_or_source_reads(self):
        with patch.object(STATUS.Integrity,'source',side_effect=AssertionError('No source reads')):
            data,failed=STATUS.build_status(self.root,STATUS.END)
        self.assertFalse(failed)
        self.assertEqual(data['status'],'ended')
        self.assertEqual(STATUS.decision(self.root,STATUS.END),{'collect':False,'publish':True})
        STATUS.write_public(self.root,data)
        self.assertEqual(STATUS.decision(self.root,STATUS.END),{'collect':False,'publish':False})
        self.assertEqual(STATUS.decision(self.root,STATUS.stamp('2027-09-09T10:17:00Z')),{'collect':False,'publish':False})

    def test_pending_one_book_capture_is_current_without_completion_or_edge_claim(self):
        self.manifest()
        data,failed=STATUS.build_status(self.root,self.now,'success',self.started,'123','1')
        self.assertFalse(failed)
        self.assertEqual(data['status'],'current')
        self.assertEqual(data['latest']['counts']['paired_games'],1)
        self.assertEqual(data['latest']['counts']['paired_two_book_games'],0)
        self.assertEqual(data['archived_scheduled_attempts'],1)
        for key in ('models_fitted','verified_completed_reports'):
            self.assertEqual(data[key],0)
        for key in ('performance_evaluated','active_policy_changed','edge_established'):
            self.assertIs(data[key],False)
        self.assertNotIn('DO-NOT-PUBLISH',json.dumps(data))
        self.assertNotIn('source_teams',json.dumps(data))
        self.assertEqual(data['registered_slots'],245)

    def test_nonpending_is_explicitly_unverified_and_empty_transport_is_not_all_available(self):
        row,path=self.manifest()
        row['counts'].update(pending_reports=0,nonpending_unverified_reports=1)
        path.write_text(json.dumps(row))
        data,_=STATUS.build_status(self.root,self.now)
        self.assertEqual(data['latest']['counts']['nonpending_unverified_reports'],1)
        self.assertEqual(data['verified_completed_reports'],0)
        row['counts']={k:0 for k in STATUS.COUNT_FIELDS}
        row['counts']['total_requests']=1
        row['reports'],row['rows']=[],[]
        path.write_text(json.dumps(row))
        data,failed=STATUS.build_status(self.root,self.now,'success')
        self.assertFalse(failed)
        self.assertEqual(data['latest']['counts']['source_reports'],0)
        self.assertIn('Empty rows do not mean players are available',data['report_note'])

    def test_repeated_manual_and_scheduled_counts_do_not_claim_unique_reports(self):
        self.manifest()
        self.manifest('456',trigger='workflow_dispatch')
        data,_=STATUS.build_status(self.root,self.now)
        self.assertEqual((data['archived_scheduled_attempts'],data['archived_manual_attempts']),(1,1))
        self.assertEqual(data['observation_totals']['source_reports'],2)
        self.assertIn('not distinct reports',data['counting_note'])

    def test_partial_and_no_manifest_failure_are_published_as_attention(self):
        self.manifest(status='partial')
        for outcome in ('failure','success'):
            data,failed=STATUS.build_status(self.root,self.now,outcome,self.started,'123','1')
            self.assertTrue(failed)
            self.assertEqual(data['status'],'attention')
            self.assertEqual(data['archived_runs'],1)
        data,failed=STATUS.build_status(self.root,self.now,'failure',self.started,'999','1')
        self.assertTrue(failed)
        self.assertFalse(data['last_workflow_attempt']['matching_manifest'])

    def test_current_attempt_identity_start_and_prerequisite_failure_cannot_reuse_old_success(self):
        self.manifest()
        for outcome,start,run,attempt in [('success',self.started,'999','1'),('success',self.started,'123','2'),
              ('success',self.started+timedelta(seconds=1),'123','1'),('skipped',self.started,'123','1'),
              ('cancelled',self.started,'123','1'),('',self.started,'123','1')]:
            with self.subTest(outcome=outcome,start=start,run=run,attempt=attempt):
                data,failed=STATUS.build_status(self.root,self.now,outcome,start,run,attempt)
                self.assertTrue(failed)
                self.assertEqual(data['status'],'attention')

    def test_staleness_uses_receipt_time_not_publication_time(self):
        self.manifest()
        data,failed=STATUS.build_status(self.root,self.now+timedelta(hours=5))
        self.assertFalse(failed)
        self.assertEqual(data['status'],'stale')

    def test_recorded_git_source_is_used_even_when_working_source_changes(self):
        self.manifest()
        (self.root/'model'/STATUS.PROTOCOL).write_text('later edit')
        data,failed=STATUS.build_status(self.root,self.now,'success')
        self.assertFalse(failed)
        self.assertEqual(data['archived_runs'],1)

    def amended_manifest(self, run='456'):
        row,path=self.manifest(run)
        row.update(STATUS.AMENDED_FIELDS)
        row['source_hashes'][STATUS.AMENDMENT]=hashlib.sha256((self.root/'model'/STATUS.AMENDMENT).read_bytes()).hexdigest()
        path.write_text(json.dumps(row))
        return row,path

    def test_original_and_amended_manifests_keep_separate_exact_provenance_and_labels(self):
        original,path=self.manifest()
        original_bytes=path.read_bytes()
        self.amended_manifest()
        data,failed=STATUS.build_status(self.root,self.now,'success')
        self.assertFalse(failed)
        self.assertEqual((data['archived_runs'],data['invalid_manifests']),(2,0))
        self.assertEqual(data['latest']['collector_version'],'acc-availability-collector-v2')
        self.assertEqual(data['latest']['execution_revision'],2)
        self.assertEqual(data['latest']['quota_policy'],'bounded_calls_when_headers_missing')
        self.assertEqual(data['latest']['amendment'],STATUS.AMENDMENT)
        self.assertEqual(path.read_bytes(),original_bytes)
        first=STATUS.manifest_summary(STATUS.Integrity(self.root),path,self.now)
        self.assertEqual(first['collector_version'],STATUS.CAPTURE_SCHEMA)
        self.assertEqual(first['execution_revision'],1)
        self.assertIsNone(first['amendment'])
        self.assertIsNone(first['quota_policy'])
        self.assertEqual(set(original['source_hashes']),set(STATUS.SOURCES))

    def test_amendment_metadata_and_its_recorded_git_pin_are_required_together(self):
        original,path=self.amended_manifest()
        for fault in ('version','revision','quota_policy','amendment','missing_field','missing_pin','wrong_pin','extra_pin','legacy_metadata_with_amendment_pin'):
            with self.subTest(fault=fault):
                row=json.loads(json.dumps(original))
                if fault=='version': row['collector_version']='acc-availability-collector-v3'
                if fault=='revision': row['execution_revision']=2.0
                if fault=='quota_policy': row['quota_policy']='unlimited'
                if fault=='amendment': row['amendment']='reports/OTHER.md'
                if fault=='missing_field': del row['quota_policy']
                if fault=='missing_pin': del row['source_hashes'][STATUS.AMENDMENT]
                if fault=='wrong_pin': row['source_hashes'][STATUS.AMENDMENT]='0'*64
                if fault=='extra_pin': row['source_hashes']['reports/OTHER.md']='0'*64
                if fault=='legacy_metadata_with_amendment_pin':
                    for key in STATUS.AMENDED_FIELDS: del row[key]
                path.write_text(json.dumps(row))
                data,failed=STATUS.build_status(self.root,self.now,'success')
                self.assertTrue(failed)
                self.assertEqual((data['archived_runs'],data['invalid_manifests']),(0,1))

    def test_older_amendment_source_is_not_reinterpreted_by_a_later_working_edit(self):
        self.amended_manifest()
        (self.root/'model'/STATUS.AMENDMENT).write_text('later version; never replace the original recorded meaning')
        data,failed=STATUS.build_status(self.root,self.now,'success')
        self.assertFalse(failed)
        self.assertEqual(data['archived_runs'],1)

    def test_invalid_metadata_hashes_and_namespace_are_withheld(self):
        original,path=self.manifest()
        for fault in ('source','workflow','cohort','receipt_reference','count','identity','future','reversed','scope','window','duplicate_receipt','request_cap','naive'):
            with self.subTest(fault=fault):
                row=json.loads(json.dumps(original))
                if fault=='source': row['source_hashes'][STATUS.PROTOCOL]='0'*64
                if fault=='workflow': row['workflow_sha256']='0'*64
                if fault=='cohort': row['cohort_sha256']='0'*64
                if fault=='receipt_reference': row['receipts']=['../private.json']
                if fault=='count': row['counts']['paired_two_book_games']=2
                if fault=='identity': row['run_id']='different'
                if fault=='future': row['capture_completed_at']=STATUS.iso(self.now+timedelta(hours=1))
                if fault=='reversed': row['capture_completed_at']=STATUS.iso(self.started-timedelta(seconds=1))
                if fault=='scope': row['verified_completed_reports']=1
                if fault=='window': row['capture_started_at']=STATUS.iso(STATUS.START-timedelta(seconds=1))
                if fault=='duplicate_receipt': row['receipts']*=2;row['counts']['total_requests']=2
                if fault=='request_cap': row['counts']['total_requests']=28
                if fault=='naive': row['capture_started_at']='2026-09-09T10:17:00'
                path.write_text(json.dumps(row))
                data,failed=STATUS.build_status(self.root,self.now,'success')
                self.assertTrue(failed)
                self.assertEqual((data['archived_runs'],data['invalid_manifests']),(0,1))

    def test_corrupt_raw_body_and_receipt_hash_do_not_become_usable_counts(self):
        row,_=self.manifest()
        receipt_path=self.root/'model'/row['receipts'][0]
        receipt_bytes=receipt_path.read_bytes()
        receipt=json.loads(receipt_bytes)
        body_path=self.root/'model'/receipt['body_path']
        original_body=body_path.read_bytes()
        for bad in (b'not gzip',gzip.compress(b'tampered',mtime=0)):
            body_path.write_bytes(bad)
            data,failed=STATUS.build_status(self.root,self.now,'success')
            self.assertTrue(failed)
            self.assertEqual(data['invalid_manifests'],1)
        body_path.write_bytes(original_body)
        receipt['status_code']=500
        receipt_path.write_text(json.dumps(receipt))
        data,failed=STATUS.build_status(self.root,self.now,'success')
        self.assertTrue(failed)
        self.assertEqual(data['invalid_manifests'],1)

    def test_body_withholding_is_retained_as_failed_receipt_not_missing_archive_corruption(self):
        row,path=self.manifest(status='partial')
        receipt=self.receipt(withheld=True)
        row['receipts'].append(receipt['receipt_path'])
        row['counts']['total_requests']=2
        path.write_text(json.dumps(row))
        data,failed=STATUS.build_status(self.root,self.now,'failure')
        self.assertTrue(failed)
        self.assertEqual(data['invalid_manifests'],0)
        self.assertEqual(data['latest']['counts']['failed_requests'],1)

    def test_symlinked_namespace_cannot_redirect_receipt_reads(self):
        row,_=self.manifest()
        receipts=self.root/STATUS.ARCHIVE/'receipts'
        external=self.root/'different-receipts'
        receipts.rename(external)
        receipts.symlink_to(external,target_is_directory=True)
        data,failed=STATUS.build_status(self.root,self.now,'success')
        self.assertTrue(failed)
        self.assertEqual((data['archived_runs'],data['invalid_manifests']),(0,1))

    def test_outside_window_manifest_has_zero_observations_and_needs_no_frozen_sources(self):
        row,path=self.manifest(status='outside_pilot',capture_started_at=STATUS.iso(STATUS.END),capture_completed_at=STATUS.iso(STATUS.END))
        for key in ('source_hashes','git_commit','workflow_sha256','cohort_path','cohort_sha256','current_receipt'):
            row.pop(key)
        row.update(receipts=[],rows=[],reports=[],counts={k:0 for k in STATUS.COUNT_FIELDS})
        path.write_text(json.dumps(row))
        data,failed=STATUS.build_status(self.root,STATUS.END)
        self.assertFalse(failed)
        self.assertEqual(data['outside_window_attempts'],1)
        self.assertEqual(data['archived_scheduled_attempts'],0)


class AvailabilityWorkflowTests(unittest.TestCase):
    def test_fixed_crons_manual_only_and_shared_serial_publication(self):
        text=(SCRIPT.parents[1]/'.github/workflows/availability.yml').read_text()
        crons=re.findall(r"cron: '([^']+)'",text)
        self.assertEqual(crons,['17,47 0,1,2,10-23 9-16 9 *','17 6 9-16 9 *'])
        self.assertEqual(text.count('timezone: America/New_York'),2)
        self.assertIn('workflow_dispatch:',text)
        self.assertNotIn('  push:',text)
        self.assertIn('group: daily-totals-pages',text)
        self.assertIn('cancel-in-progress: false',text)
        self.assertLess(text.index('availability_status.py decision'),text.index('pip install'))
        self.assertIn('python -m ncaaf_model.availability_collector --root .',text)
        self.assertEqual(text.count('secrets.'),1)
        self.assertIn('secrets.ODDS_API_IO_KEY',text)

    def test_failure_status_is_archived_deployed_then_failed_without_touching_policies(self):
        text=(SCRIPT.parents[1]/'.github/workflows/availability.yml').read_text()
        self.assertIn("if: always() && steps.decision.outputs.publish == 'true'",text)
        self.assertIn("if: always() && steps.publication.outcome == 'success'",text)
        self.assertIn("steps.archive.outcome == 'success'",text)
        self.assertIn("needs.collect.outputs.archive_uploaded == 'true'",text)
        self.assertIn('--attempt-started-at "$ATTEMPT_STARTED_AT"',text)
        self.assertIn("paths = [Path('model/data/runtime/availability'), Path('site/data/availability.json')]",text)
        self.assertNotIn('model/ledger',text)
        self.assertNotIn('ncaaf_model.runtime',text)
        self.assertNotIn('weather_revision_study',text)
        self.assertLess(text.index('uses: actions/deploy-pages@v4'),text.index('exit 1'))
        self.assertIn('model/tests/test_availability_*.py',text)


if __name__ == '__main__':
    unittest.main()
