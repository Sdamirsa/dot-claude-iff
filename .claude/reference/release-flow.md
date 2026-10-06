# Release flow - branches, tags, zips (home repo only)

How dot-claude-iff moves from work to a published release. Home-only: `distctl.py` drops this
file from both zips (`HOME_ONLY_FILES`, distribution-boundary mechanism 3), the adopt skill
skips it on clone adoptions, and `test_dist.py` asserts it is absent from both zips.

## Branches

- **`dev`** - all work lands here. Builders work in git worktrees under `.claude/worktrees/`
  (gitignored, never packaged), cut by `statectl.py dispatch`; the main session reviews,
  merges into `dev` with `statectl.py accept` and runs the suite.
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

1. The maintainer names the version. Set `system_version` and pin the CHANGELOG section (the
   release steps in the project-memory skill).
2. **The zip step, on the release commit itself.** `python3 .claude/tools/distctl.py build`,
   then `python3 .claude/tools/distctl.py verify` (it must say fresh), then commit the zips,
   then tag. A pre-release cut from `dev` takes the same step before its tag: the tag is what
   makes CI strict about the zips, so they must be rebuilt in the commit it points at.
3. Push the branch, then the tag. `release.yml` runs the suite on both OS (strict about the
   zips: a tag is in play), rebuilds the zips, checks the rebuild equals the committed copies,
   and publishes. Watch it; never run `gh release create` by hand.

## The committed zips

The zips stay committed at `.claude/dist/`. Where they must equal a rebuild is decided in one
place, `_lib.zip_equality_required()`: on the branch `main`, on a tag, on a pull request into
`main`, and on CI for `main`. Anywhere else (`dev`, a feature branch, a builder's worktree) the
suite skips the equality test with a one-line reason; `distctl.py verify` stays strict
everywhere, and `release.yml` rebuilds and diffs before it publishes.

**The consequence, stated plainly:** between releases the zips on `dev` may lag the tree. The
copies on `main` and on every tag are guaranteed equal to a rebuild. The trade buys conflict-free
merges: builders used to rebuild the zips in their worktrees while `dev` rebuilt them too, and
two merges conflicted on binary files. Builders now never stage, commit or rebuild
`.claude/dist/`, `statectl.py accept` restores any change there before it merges, and a merge on
`dev` needs no rebuild. The ritual's POLISH still rebuilds the zips at home; only the lead
commits on the working branch, so that cannot conflict.

Three rules keep the copies that must be fresh mechanically fresh:

- **Payload rule: the working tree decides which files ship and what they contain; git only
  vetoes what it ignores.** The generator ledger hashes the working tree, and PUBLISH commits
  with `git add -A` after POLISH builds the zips, so "not gitignored" is exactly what the
  ritual's own commit tracks. The earlier rule (ship only what the git index tracks) skipped a
  file created in-session; the ledger then called the zips fresh, the commit tracked the file,
  and the zips lacked it until an unrelated input changed. A gitignored file (the private
  reference tree, a stray `.env`) still never ships, and each one skipped is printed.
- **Line endings: text ships as LF.** A Windows checkout converts LF to CRLF; distctl
  normalises every text entry back to LF, so one commit builds the same bytes on every OS.
- **Equality test.** Where `zip_equality_required()` says so, `test_dist.py` rebuilds both zips
  into a scratch directory and compares them with `.claude/dist/` byte for byte. There, while
  payload files carry uncommitted edits it still skips (the ritual's CHECK runs before POLISH
  rebuilds); on a clean tree, which is every CI run, it is strict.

## Untracked by design

- `.claude/state/heartbeat.json` - rewritten every turn. Gitignored here and in the kits'
  `.gitignore`; the Stop hook creates it, and `state/` with it, on a fresh install.
- `.claude/worktrees/` - scratch checkouts. Gitignored, excluded from the zips structurally.
