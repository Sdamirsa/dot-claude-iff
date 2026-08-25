# .claude-iff - the committed record surface

Everything lives in `.claude/`. A thing lives here **if and only if** it must be committed with
the repo *and* untouchable by every agent identity. Two things qualify:

| Path | What | Why here |
|------|------|----------|
| `obs/anchor.json` | SHA-256 head of the last sealed segment | Committed history is what makes the record tamper-**evident**: an edit to a sealed segment stops matching an anchor that git already recorded. |
| `obs/rollups/*.json` | Daily totals: token counts, cost, event and error counts | Small, allowlisted metadata. The project's history should travel with the project. |

Nothing verbatim is here, and nothing here is written by hand. `.claude/hooks/policy-gate.sh`
denies Write, Edit and mutating Bash under this directory for **every** identity, including the
main session. The only writer is `python3 .claude/tools/obsctl.py`.

## Where the rest of the record lives

Raw capture contains prompts, file contents and tool output verbatim. Measured on the system
this one was distilled from, that is about 70% of capture volume. Committing it would put every
secret an agent ever read into git history permanently. So it lives outside the repo entirely:

```
<parent>/<repo-name>_claude_iff/
  spool/           raw hook events, as captured
  sealed-raw/      sealed raw, gzip-compacted, KEPT FOREVER (the agent's ground truth)
  segments/        allowlisted metadata, sealed 0444
  raw/transcripts/ verbatim transcript copies (the completeness backstop)
  analysis/        products of `obsctl.py analyze`
  vault/           snapshots: git bundle + chat transcripts
```

A sibling folder rather than a hidden state directory, so the record stays visible and
inspectable next to the project it belongs to. Override with `record_root` in
`.claude/config/policy.json`; both the tools and the deny rule resolve it through
`_lib.record_root()`, so the gate can never point somewhere different from where capture writes.

Raw is kept forever by default. `observe.retention_days` exists as an off-by-default knob, and
`checkctl.py run --phase check` reports the record's size loudly. Nothing is ever deleted
automatically.

## Reading the record

Read it freely. To analyse it, use the one sanctioned pathway, which sends batches to a separate
model and writes labelled products into `analysis/`:

```
python3 .claude/tools/obsctl.py analyze --dry-run
```

Products reach the repo only by a human-gated copy into `.claude/research/`.
