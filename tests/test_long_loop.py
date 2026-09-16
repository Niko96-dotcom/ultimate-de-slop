"""Full multi-fix pipeline proof using a deterministic external CLI fixture."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from test_harness import SCRIPT_DIR, init_repo, minimal_finding, run, write_executable, write_findings


STUB = r'''#!/usr/bin/env python3
import json, re, sys
from pathlib import Path
prompt = sys.stdin.read()
schema = Path(sys.argv[sys.argv.index('--output-schema')+1]).name
if schema == 'fix.schema.json':
    finding = json.loads(prompt.split('Finding:\n')[-1])
    ident = finding['id']
    i = int(ident.split('-')[-1])
    p = Path('sample.py')
    p.write_text(p.read_text().replace(f'return x + 0  # f{i}', f'return x  # f{i}'))
    payload = dict(finding_id=ident, summary='Remove redundant addition preserving integer behavior',
                   changed_files=['sample.py'], checks_run=[], risks=[], status='fixed')
elif schema == 'verify.schema.json':
    ident = re.search(r'DSL-\d{6}', prompt).group()
    payload = dict(finding_id=ident, verdict='PASS', confidence=.99,
                   evidence=['tests/test_sample.py exercises all seven integer identity functions; checks passed.'],
                   concerns=[], required_follow_up=[])
else:
    payload = dict(repo_summary='No further eligible findings', review_wave_id='empty',
                   partitions_reviewed=['.'], findings=[])
text = json.dumps(payload)
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(text)
print(text)
'''


class LongLoopTests(unittest.TestCase):
    def test_seven_fixes_then_complete_sweeps_and_resume(self):
        with tempfile.TemporaryDirectory(prefix='deslop pipeline ') as tmp:
            root = Path(tmp)
            init_repo(root)
            (root/'.gitignore').write_text('.deslop/\nfake-bin/\n__pycache__/\n')
            (root/'sample.py').write_text('\n\n'.join(f'def f{i}(x: int):\n    return x + 0  # f{i}' for i in range(1,8))+'\n')
            (root/'tests').mkdir()
            (root/'tests/test_sample.py').write_text('import unittest\nimport sample\nclass IdentityTests(unittest.TestCase):\n    def test_identity(self):\n        for i in range(1,8):\n            for value in [-9, 0, 27]:\n                self.assertEqual(getattr(sample, f"f{i}")(value), value)\n')
            run(['git','add','.'],cwd=root,check=True)
            run(['git','commit','-m','fixture'],cwd=root,check=True)
            findings=[]
            for i in range(1,8):
                f=minimal_finding(f'DSL-{i:06d}', expected_checks=['python3 -m unittest discover -s tests'])
                f.update(severity='P2', title=f'Redundant integer addition in f{i}',
                         acceptance_criteria=[f'f{i} preserves integer identity; existing tests/test_sample.py passes'])
                findings.append(f)
            write_findings(root,*findings)
            fake=root/'fake-bin';fake.mkdir()
            # Avoid any live provider calls: the exact harness executable is a fixture.
            write_executable(fake/'codex', STUB)
            env={'PATH':f'{fake}{os.pathsep}{os.environ["PATH"]}','DESLOP_HARNESS':'codex'}
            result=run([str(SCRIPT_DIR/'deslop-loop.sh'),'--until-clean','--max-iterations','9','--max-review-calls','20'],cwd=root,env=env)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            state=json.loads((root/'.deslop/state.json').read_text())
            self.assertEqual(state['loop_outcome']['stop_reason'],'until_clean',result.stdout+result.stderr)
            self.assertEqual(state['loop_goal']['consumed']['fix_attempts'],7)
            actual=[json.loads(line) for line in (root/'.deslop/findings.jsonl').read_text().splitlines()]
            self.assertEqual([f['status'] for f in actual],['verified']*7)
            self.assertGreaterEqual(state['loop_progress']['consecutive_empty_review_waves'],2)
            usage=state['loop_goal']['consumed'].copy()
            again=run([str(SCRIPT_DIR/'deslop-continue.sh')],cwd=root,env=env)
            self.assertEqual(again.returncode,0,again.stdout+again.stderr)
            self.assertEqual(json.loads((root/'.deslop/state.json').read_text())['loop_goal']['consumed'],usage)
            check=run(['python3','-m','unittest','discover','-s','tests'],cwd=root)
            self.assertEqual(check.returncode,0,check.stderr)
