"""Regressions for content-derived ownership and bound verification evidence."""
import argparse
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_harness import SCRIPT_DIR, init_repo, minimal_finding, run, write_findings, write_executable
sys.path.insert(0, str(SCRIPT_DIR))
from deslop_snapshot import snapshot
from deslop_verification_policy import checks_failed, is_docs_only_path, proof_matches


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


class ProofSafetyTests(unittest.TestCase):
    def test_docs_exception_never_accepts_failed_or_empty_pass_checks(self):
        finding = minimal_finding('DSL-000001')
        finding.update(last_fix={'changed_files': ['README.md']}, no_expected_checks_reason='prose only')
        verify = {'evidence': ['Documentation text only; no executable behavior changed.']}
        for status, results in [('failed', []), ('passed', []), ('passed', [{}]),
                                ('failed', [{'command': 'make test', 'status': 'failed', 'exit_code': 1}])]:
            with self.subTest(status=status, results=results):
                self.assertTrue(checks_failed({'finding_id': finding['id'], 'status': status, 'results': results},
                                             finding_id=finding['id'], finding=finding, verify=verify))
        self.assertFalse(checks_failed({'finding_id': finding['id'], 'status': 'skipped', 'results': []},
                                      finding_id=finding['id'], finding=finding, verify=verify))

    def test_executable_docs_and_runtime_text_are_not_docs_exceptions(self):
        for path in ['docs/conf.py', 'docs/build.sh', 'requirements.txt', '../README.md', 'README.py']:
            with self.subTest(path=path):
                self.assertFalse(is_docs_only_path(path))

    def test_manifest_sees_unicode_untracked_content_and_symlink_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            path = root / 'café file.py'
            path.write_text('one')
            before = snapshot(root)
            path.write_text('two')
            self.assertNotEqual(snapshot(root), before)
            (root / 'alias').symlink_to('café file.py')
            before = snapshot(root)
            (root / 'alias').unlink()
            (root / 'alias').symlink_to('other')
            self.assertNotEqual(snapshot(root), before)
            before = snapshot(root)
            (root / '.deslop' / 'new-state.json').write_text('{}')
            self.assertEqual(snapshot(root), before)

    def test_proof_rejects_new_attempt_or_content_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            (root / 'sample.py').write_text('value = 1\n')
            finding = minimal_finding('DSL-000001')
            finding['last_fix'] = {'run_dir': 'attempt-1'}
            proof = {'finding_id': finding['id'], 'fix_run': 'attempt-1', 'worktree_manifest': snapshot(root)}
            self.assertTrue(proof_matches(proof, finding, root))
            finding['last_fix']['run_dir'] = 'attempt-2'
            self.assertFalse(proof_matches(proof, finding, root))
            finding['last_fix']['run_dir'] = 'attempt-1'
            (root / 'sample.py').write_text('value = 2\n')
            self.assertFalse(proof_matches(proof, finding, root))

    def test_read_only_policies_ignore_write_overrides(self):
        runner = module('proof_runner', 'deslop-agent-runner.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schema = root / 'schema.json'
            schema.write_text('{}')
            args = argparse.Namespace(root=root, sandbox='danger-full-access', kind='verify',
                                      schema=schema, last_message=root/'last', prompt=root/'prompt',
                                      permission_mode='bypassPermissions', add_dir=[])
            codex = runner.codex_adapter(args, None).command
            self.assertEqual(codex[codex.index('--sandbox')+1], 'read-only')
            claude = runner.claude_adapter(args, None).command
            self.assertEqual(claude[claude.index('--permission-mode')+1], 'plan')
            self.assertEqual(claude[claude.index('--tools')+1], 'Read,Glob,Grep')
            cursor = runner.cursor_adapter(args, None).command
            self.assertEqual(cursor[cursor.index('--mode')+1], 'ask')
            adapter = runner.opencode_adapter(args, None)
            with patch.dict(os.environ, {'OPENCODE_CONFIG_CONTENT': '{}'}):
                config = json.loads(runner.child_environment(args, adapter)['OPENCODE_CONFIG_CONTENT'])
            self.assertEqual(config['agent']['deslop_verifier']['permission']['edit'], 'deny')
            self.assertEqual(config['permission']['bash'], 'deny')

    def test_commit_or_revert_refuses_preexisting_changes(self):
        finalizer = module('proof_finalizer', 'deslop-finalize.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            (root / 'user.py').write_text('valuable work')
            before = root / '.deslop/before.txt'
            before.write_text(' M user.py\n')
            finding = minimal_finding('DSL-000001')
            finding['last_fix'] = {'changed_files': ['sample.py'], 'snapshot_paths': {'status_before': str(before)}}
            with self.assertRaises(SystemExit):
                finalizer.mutation_paths(root, finding)
            self.assertEqual((root/'user.py').read_text(), 'valuable work')

    def test_resume_cannot_manufacture_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            finding = minimal_finding('DSL-000001')
            finding['status'] = 'fixing'
            write_findings(root, finding)
            result = run([sys.executable, str(SCRIPT_DIR/'deslop-resume.py'), finding['id'], '--as', 'verified'], cwd=root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('bound checks', result.stderr)

    def test_verified_finding_can_recur_after_relevant_source_change(self):
        arbiter = module('proof_arbiter', 'deslop-arbitrate.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            source = root/'sample.py'
            source.write_text('value = 1\n')
            candidate = minimal_finding('DSL-000001', expected_checks=['python3 -m py_compile sample.py'])
            candidate['status'] = 'verified'
            candidate['verification'] = {'worktree_manifest': snapshot(root)}
            write_findings(root, candidate)
            review = root/'.deslop/review.json'
            review.write_text(json.dumps(dict(repo_summary='review', review_wave_id='wave',
                                              partitions_reviewed=['.'], findings=[candidate])))
            first = arbiter.arbitrate(root, review, None, True)
            self.assertEqual(first['accepted'], [])
            source.write_text('value = 2\n')
            second = arbiter.arbitrate(root, review, None, True)
            self.assertEqual(len(second['accepted']), 1)

    def test_confidence_cannot_bypass_threshold_with_nan_or_infinity(self):
        arbiter = module('proof_arbiter_confidence', 'deslop-arbitrate.py')
        for confidence in [float('nan'), float('inf'), 2, True]:
            with self.subTest(confidence=confidence):
                finding = minimal_finding('DSL-000001', expected_checks=['python3 -m py_compile sample.py'])
                finding['confidence'] = confidence
                self.assertTrue(any('finite number' in reason for reason in arbiter.validate_candidate(finding, {})))

    def test_new_untracked_file_obeys_line_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            (root/'base.py').write_text('value = 1\n')
            run(['git', 'add', '.'], cwd=root, check=True)
            run(['git', 'commit', '-m', 'base'], cwd=root, check=True)
            write_findings(root, minimal_finding('DSL-000001', files=['new.py']))
            fake = root/'fake-bin'
            fake.mkdir()
            payload = dict(finding_id='DSL-000001', summary='large file', changed_files=['new.py'],
                           checks_run=[], risks=[], status='fixed')
            script = "#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n"
            script += "Path('new.py').write_text('value = 1\\n' * 501)\n"
            script += "text = json.dumps(" + repr(payload) + ")\n"
            script += "Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(text)\nprint(text)\n"
            write_executable(fake/'codex', script)
            result = run([str(SCRIPT_DIR/'deslop-fix.sh'), '--allow-dirty', 'DSL-000001'], cwd=root,
                         env={'PATH': str(fake)+os.pathsep+os.environ['PATH'], 'DESLOP_HARNESS': 'codex'})
            finding = json.loads((root/'.deslop/findings.jsonl').read_text().splitlines()[0])
            self.assertEqual(finding['status'], 'needs_human', result.stdout+result.stderr)
            self.assertIn('change budget', finding['block_reason'])
            self.assertEqual(finding['last_fix']['attempt_changed_files'], 1)
            self.assertGreaterEqual(finding['last_fix']['attempt_changed_lines'], 501)
