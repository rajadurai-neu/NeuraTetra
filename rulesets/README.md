# Rule sets

YAML rules loaded by the API. **Starter content: compliance staff must review before use.**

- `manifest.yaml`: bundle version (stored on every assessment) and which files to load
- `part599.yaml`, `omh.yaml`, `soap.yaml`, `agency.yaml`: deterministic rules
- `quality.yaml`: clinical quality measures scored by the AI reviewer
- `samples/`: synthetic notes with expected results; the tests check every rule change against them

To change a rule: edit the YAML, bump `version` in `manifest.yaml`, run `.venv/bin/python -m pytest`.
