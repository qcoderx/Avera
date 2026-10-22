# Mock bank repair demo

This package powers the frontend `/bank` route. It owns one controlled incident:
Gateway A becomes unavailable while Bank X remains pinned to that route. Four
customers report duplicate debits created by the resulting timeout replay.

`POST /bank-demo/trigger` restores the known-buggy module in an isolated temporary
workspace, reproduces two ledger debits, and starts one repair worker. The worker
reads and edits only `bank_Demo/workspace/transfer_service.py`, runs the bundled
`unittest` regression suite in a subprocess, and repeats the original journey.
The API reports `resolved` only after Bank X fails over to Gateway B, produces one
debit, and the duplicate debit is reversed.

When the configured GPT model is available it proposes the replacement module.
The replacement passes a strict AST capability policy before it can be written or
executed. A deterministic safe repair keeps the local demo usable without a model.
Neither path can access the repository outside the temporary workspace.

Run the focused proof:

```bash
cd backend
.venv/bin/python -m pytest -q tests/bank_Demo
```
