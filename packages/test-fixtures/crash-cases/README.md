# crash-cases

Use this directory for reduced failure-state fixtures that exercise sync
recovery paths and cannot be generated cheaply during a unit or E2E test.

## Candidate Cases

1. Partial `sync_apply_journal` with staged blobs.
2. Partial `commit_intent_journal` after remote submit uncertainty.
3. Corrupt staging payload isolated into `.noteapp/staging-orphans/`.
4. Conflict artifact preservation under `.noteapp/conflict-orphans/`.
5. Legacy local state requiring migration before sync can resume.

## Required Metadata

Each case must include a README that records:

1. How the state was produced.
2. Which command consumes it.
3. The expected recovery result.
4. Why the case cannot be generated quickly inside the test itself.
