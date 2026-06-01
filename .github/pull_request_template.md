## Summary

- 

## Validation

- [ ] `python3 -m compileall scripts tests`
- [ ] `bash -n scripts/*.sh scripts/install/*.sh`
- [ ] `python3 -m unittest discover -s tests -v`

## Safety Notes

- [ ] This keeps fixer scope bounded to one accepted finding.
- [ ] This does not add hidden writes outside the documented runtime paths.
- [ ] Public logs or examples contain no private repository contents.
