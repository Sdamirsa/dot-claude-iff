# Release flow - branches, tags, zips (home repo only)

How dot-claude-iff moves from work to a published release. Home-only: `distctl.py` drops this
file from both zips (`HOME_ONLY_FILES`, distribution-boundary mechanism 3), the adopt skill
skips it on clone adoptions, and `test_dist.py` asserts it is absent from both zips.

## Branches

- **`dev`** - all work lands here. Builders work in git worktrees under `.claude/worktrees/`
  (gitignored, never packaged); the main session reviews, merges into `dev` and runs the suite.
- **`main`** - the last stable release, nothing else. It changes only through one pull request
  `dev` -> `main`, opened when a stable release is signed off.
- **CI** - `.github/workflows/ci.yml` runs the whole suite on every push to `dev` or `main` and
  on every pull request, on ubuntu and windows.

## Versions and tags

- Grammar: `X.Y.Z` (stable) or `X.Y.Z-alpha.N`, `X.Y.Z-beta.N`, `X.Y.Z-rc.N` (pre-release),
  no leading zeros. The tag is `v` + version; the CHANGELOG heading is
  `## vX.Y.Z[-pre.N] - YYYY-MM-DD`. `_lib.parse_version` / `_lib.is_prerelease` are the one
  parser; `changelog_parity` and `release.yml` both use it, and the heading match is exact
  (`_lib.changelog_section`): `v0.3.0` is never satisfied by `## v0.3.0-alpha.1`.
- **Pre-release**: tag a commit on `dev`. `release.yml` publishes it as a GitHub pre-release
  with `--prerelease --latest=false`, so "latest" stays on the last stable.
- **Stable**: merge the `dev` -> `main` PR, tag on `main`. Published as a full release; becomes
  "latest".
- Any other `v*` tag fails the release job instead of publishing.
- **Never force-push, move or delete a published tag.** A broken release is fixed forward: a
  new commit and the next tag (`-alpha.N+1`, or the next patch).

## Cutting one

1. The maintainer names the version. Set `system_version`, pin the CHANGELOG section (the
   release steps in the project-memory skill), and let the ritual's POLISH rebuild the zips.
2. Commit the release with its zips. `python3 .claude/tools/distctl.py verify` must say fresh.
3. Push the branch, then the tag. `release.yml` runs the suite on both OS, rebuilds the zips,
   checks the rebuild equals the committed copies, and publishes. Watch it; never run
   `gh release create` by hand.

## The committed zips

The zips stay committed at `.claude/dist/`, so they must never be stale. Three rules make that
mechanical:

- **Payload rule: the working tree decides which files ship and what they contain; git only
  vetoes what it ignores.** The generator ledger hashes the working tree, and PUBLISH commits
  with `git add -A` after POLISH builds the zips, so "not gitignored" is exactly what the
  ritual's own commit tracks. The earlier rule (ship only what the git index tracks) skipped a
  file created in-session; the ledger then called the zips fresh, the commit tracked the file,
  and the zips lacked it until an unrelated input changed. A gitignored file (the private
  reference tree, a stray `.env`) still never ships, and each one skipped is printed.
- **Line endings: text ships as LF.** A Windows checkout converts LF to CRLF; distctl
  normalises every text entry back to LF, so one commit builds the same bytes on every OS.
- **Equality test.** `test_dist.py` rebuilds both zips into a scratch directory and compares
  them with `.claude/dist/` byte for byte. While payload files carry uncommitted edits it skips
  (POLISH is about to rebuild); on a clean tree, which is every CI run, it is strict. Rebuild
  with `distctl.py build` (or the ritual) and commit the zips with the change.

## Untracked by design

- `.claude/state/heartbeat.json` - rewritten every turn. Gitignored here and in the kits'
  `.gitignore`; the Stop hook creates it, and `state/` with it, on a fresh install.
- `.claude/worktrees/` - scratch checkouts. Gitignored, excluded from the zips structurally.
