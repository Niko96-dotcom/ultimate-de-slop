## Problem and result

- Problem:
- Result (observable behavior after this change):

## Exact validation

Paste the exact commands run and their results:

```sh
make ci
```

```sh
python3 -m unittest tests.test_harness.HarnessTests.test_deterministic_proof_run -v
```

- [ ] `python3 -m compileall scripts tests`
- [ ] `bash -n scripts/*.sh scripts/install/*.sh`
- [ ] `python3 -m unittest discover -s tests -v`
- Focused test(s) (if applicable):

## Safety notes

- [ ] Fixer scope stays bounded to one accepted finding.
- [ ] No hidden writes outside the documented `.deslop/` and single-finding paths.
- [ ] Shared logs or examples contain no secrets, private repository contents, or raw agent transcripts.
