"""Exercise native handoffs through the actual stage pipeline, with no provider CLI."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from test_harness import SCRIPT_DIR, init_repo, minimal_finding, run, write_findings

spec = importlib.util.spec_from_file_location('cloud', SCRIPT_DIR/'deslop-cloud.py')
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)


class CloudTests(unittest.TestCase):
    def invoke(self, repo, *args):
        result = run([sys.executable, str(SCRIPT_DIR/'deslop-cloud.py'), *args], cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def fixture(self, repo):
        init_repo(repo)
        (repo/'.gitignore').write_text('.deslop/\n__pycache__/\n')
        (repo/'sample.py').write_text('def identity(x: int):\n    return x + 0\n')
        (repo/'tests').mkdir()
        (repo/'tests/test_sample.py').write_text('import unittest\nimport sample\nclass T(unittest.TestCase):\n    def test_identity(self):\n        self.assertEqual(sample.identity(4),4)\n')
        run(['git','add','.'],cwd=repo,check=True)
        run(['git','commit','-m','fixture'],cwd=repo,check=True)

    def test_fix_verify_clean_and_resume_without_cli(self):
        with tempfile.TemporaryDirectory(prefix='native cloud ') as tmp:
            repo=Path(tmp); self.fixture(repo)
            finding=minimal_finding('DSL-000001', expected_checks=['python3 -m unittest discover -s tests'])
            write_findings(repo, finding)
            self.invoke(repo,'start','--','--until-clean','--max-iterations','2','--max-review-calls','10','--max-seconds','60')
            kinds=[]
            try:
                for _ in range(60):
                    report=self.invoke(repo,'poll','--wait','1')
                    if report['session']['status']=='finished':
                        break
                    for req in report['requests']:
                        kinds.append(req['kind'])
                        if req['kind']=='fix':
                            self.assertFalse(req['readonly'])
                            (repo/'sample.py').write_text('def identity(x: int):\n    return x\n')
                            result=dict(finding_id='DSL-000001',summary='Remove redundant addition',changed_files=['sample.py'],checks_run=[],risks=[],status='fixed')
                        elif req['kind']=='verify':
                            self.assertTrue(req['readonly'])
                            result=dict(finding_id='DSL-000001',verdict='PASS',confidence=.99,evidence=['Existing tests/test_sample.py covers integer identity and passes.'],concerns=[],required_follow_up=[])
                        else:
                            self.assertTrue(req['readonly'])
                            result=dict(repo_summary='clean',review_wave_id='empty',partitions_reviewed=['.'],findings=[])
                        result_file=repo/'.deslop/tmp/result.json'
                        result_file.write_text(json.dumps(result))
                        self.invoke(repo,'submit',req['id'],str(result_file))
                        while any(x['id']==req['id'] for x in cloud.pending(repo)):
                            time.sleep(.05)
                else:
                    self.fail('controller did not finish')
                self.assertEqual(report['session']['exit_code'],0,(repo/'.deslop/cloud-loop.log').read_text())
                state=json.loads((repo/'.deslop/state.json').read_text())
                self.assertEqual(state['loop_outcome']['stop_reason'],'until_clean')
                self.assertEqual(state['loop_goal']['consumed']['fix_attempts'],1)
                self.assertIn('verify',kinds)
                self.assertEqual(json.loads((repo/'.deslop/findings.jsonl').read_text())['status'],'verified')
                usage=state['loop_goal']['consumed'].copy()
                self.invoke(repo,'start','--','--continue')
                for _ in range(20):
                    time.sleep(.1)
                    again=self.invoke(repo,'poll','--wait','1')
                    if again['session']['status']=='finished': break
                self.assertFalse(again['requests'])
                self.assertEqual(json.loads((repo/'.deslop/state.json').read_text())['loop_goal']['consumed'],usage)
            finally:
                self.invoke(repo,'stop')

    def test_stale_responses_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.fixture(repo)
            result=repo/'.deslop/result.json';result.write_text('{}')
            with self.assertRaisesRegex(ValueError,'No live'):
                cloud.submit(repo,'stale',result)

    def test_concurrent_start_rejected_and_stop_cancels_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.fixture(repo)
            self.invoke(repo,'start','--','--review-only')
            try:
                for _ in range(10):
                    report=self.invoke(repo,'poll','--wait','1')
                    if report['requests']:break
                self.assertTrue(report['requests'])
                duplicate=run([sys.executable,str(SCRIPT_DIR/'deslop-cloud.py'),'start'],cwd=repo)
                self.assertNotEqual(duplicate.returncode,0)
                self.invoke(repo,'stop')
                for _ in range(20):
                    report=self.invoke(repo,'poll','--wait','1')
                    if report['session']['status']=='finished':break
                    time.sleep(.1)
                self.assertEqual(report['session']['status'],'finished')
                self.assertFalse(report['requests'])
                self.assertNotEqual(report['session']['exit_code'],0)
            finally:
                self.invoke(repo,'stop')

    def test_native_review_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.fixture(repo)
            self.invoke(repo,'start','--','--review-only')
            try:
                for _ in range(10):
                    report=self.invoke(repo,'poll','--wait','1')
                    if report['requests']:break
                req=report['requests'][0]
                (repo/'sample.py').write_text('unauthorized = True\n')
                result=repo/'.deslop/tmp/result.json'
                result.write_text(json.dumps(dict(repo_summary='bad',review_wave_id='empty',partitions_reviewed=['.'],findings=[])))
                self.invoke(repo,'submit',req['id'],str(result))
                for _ in range(20):
                    time.sleep(.1)
                    report=self.invoke(repo,'poll','--wait','1')
                    if report['session']['status']=='finished':break
                self.assertNotEqual(report['session']['exit_code'],0)
                diagnostic=json.loads((Path(req['prompt']).parent/'runner.json').read_text())
                self.assertEqual(diagnostic['status'],'read_only_mutation')
            finally:
                self.invoke(repo,'stop')

    def test_installer_excludes_nested_cli_config(self):
        installer_spec=importlib.util.spec_from_file_location('installer',SCRIPT_DIR/'install/install-skill.py')
        installer=importlib.util.module_from_spec(installer_spec)
        installer_spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);source=base/'source';source.mkdir()
            (source/'SKILL.md').write_text('skill')
            (source/'.opencode').mkdir()
            (source/'.opencode/private.json').write_text('private fixture')
            target=base/'target';installer.copy_tree(source,target)
            self.assertTrue((target/'SKILL.md').exists())
            self.assertFalse((target/'.opencode').exists())

    def test_submission_rejects_nonobject_duplicate_and_external_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);self.fixture(repo)
            folder=repo/'.deslop/runs/request';folder.mkdir()
            request=dict(id='live',pid=os.getpid(),status='pending',root=str(repo.resolve()),
                         expires_at=time.time()+30,response=str(repo/'outside.json'))
            (folder/'native-request.json').write_text(json.dumps(request))
            result=repo/'.deslop/result.json';result.write_text('[]')
            with self.assertRaisesRegex(ValueError,'JSON object'):
                cloud.submit(repo,'live',result)
            result.write_text('{}');cloud.submit(repo,'live',result)
            self.assertFalse((repo/'outside.json').exists())
            self.assertTrue((folder/'native-response.json').exists())
            with self.assertRaisesRegex(ValueError,'already submitted'):
                cloud.submit(repo,'live',result)
