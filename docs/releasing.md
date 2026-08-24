# Releasing

This guide is for maintainers preparing a Lemmy Vote Viewer release. It covers
the project workflow from choosing a version through merging, tagging, and
publishing the GitHub release.

Operators updating an installed viewer should instead follow
[Updating an existing deployment](../README.md#updating-an-existing-deployment).

## Release model

`VERSION` is the single source of truth for the application version. It
contains a plain semantic version without a leading `v`, for example:

```text
0.9.0
```

The application reads this file at startup. The value is displayed in the UI
footer and included in the authentication HTTP user agent. The Docker image
copies the same file into `/app/VERSION`.

Git tags and GitHub releases add the `v` prefix:

```text
v0.9.0
```

Do not hard-code the version in Python or the HTML templates.

## Choosing the version

The project uses `MAJOR.MINOR.PATCH` versions. While the project remains in the
`0.x` series, use the following practical policy:

### Patch release

Increment the patch component, such as `0.8.2` to `0.8.3`, for:

- bug fixes;
- query-performance improvements that preserve visible behavior;
- security hardening that does not change configuration expectations;
- small layout, wording, or link-behavior corrections; and
- internal refactoring with no new user-facing capability.

Documentation-only changes normally do not need a version bump or release.

### Minor release

Increment the minor component and reset the patch component, such as `0.8.2`
to `0.9.0`, for:

- a new user-visible feature or view;
- a significant new configuration option;
- a material change to authentication or authorization;
- support for a new Lemmy database schema; or
- a substantial change to data interpretation or deployment behavior.

### Major release

Use `1.0.0` when the project is ready to declare a stable compatibility and
configuration contract. After 1.0, increment the major component for breaking
changes to that contract.

When uncertain between patch and minor, consider whether an operator or user
would describe the release as gaining a capability. If so, prefer a minor
release. Record any required operator action prominently in the release notes,
regardless of the version size.

## Prepare the branch

Start from an up-to-date, clean `main` branch:

```sh
git switch main
git pull --ff-only origin main
git status --short --branch
```

Create a descriptively named branch:

```sh
git switch -c FeatureName
```

Keep unrelated work out of the release branch. Before changing files, confirm
the branch again:

```sh
git branch --show-current
git status --short --branch
```

## Update the version

Edit `VERSION` so it contains only the new version and a final newline. Do not
include the `v` prefix. Confirm the result:

```sh
cat VERSION
git diff -- VERSION
```

The version bump should be included in the pull request that will become the
release. Do not create the release tag on the feature branch.

Review the deployment examples in `README.md`. Update hard-coded example tags
when the new release should become the recommended deployment version.

## Verification before the pull request

At minimum, run whitespace, compilation, and unit-test checks:

```sh
git diff --check
python3 -m py_compile app.py vote_viewer/*.py
python3 -m unittest discover -s tests -v
```

If the host does not have the Python dependencies installed, build an image and
run the tests using its environment:

```sh
docker build -t lemmy-vote-viewer-release-test .

docker run --rm \
  -e PYTHONPATH=/src \
  -v "$PWD:/src:ro" \
  -w /tmp \
  lemmy-vote-viewer-release-test \
  python -m unittest discover -s /src/tests -v
```

Confirm that the built image contains the intended version:

```sh
docker run --rm --entrypoint cat \
  lemmy-vote-viewer-release-test /app/VERSION
```

See the complete [unit-test documentation](unit-tests.md). Database or query
changes should also be tested against a copied production database. Lemmy
schema changes must follow the [database compatibility procedure](database-compatibility.md).

For UI changes, check representative desktop and narrow/mobile layouts. For
SQL changes, test both representative and large result sets, including cold or
semi-cold runs where performance is relevant.

## Commit and push

Review the complete change set before committing:

```sh
git status --short --branch
git diff
git diff --check
```

Stage only the intended files, then commit with a concise outcome-focused
message:

```sh
git add VERSION path/to/changed-file
git commit -m "Describe the release outcome"
```

Push the branch and establish its upstream:

```sh
git push -u origin FeatureName
```

## Create and merge the pull request

Write the pull request body as Markdown in a file. Using `--body-file` preserves
real line breaks and avoids publishing visible `\n` escape sequences:

```sh
gh pr create \
  --base main \
  --head FeatureName \
  --title "Describe the release" \
  --body-file /path/to/pull-request.md
```

A useful pull request description includes:

- what changed and why;
- user-visible behavior;
- security, privacy, or database implications;
- new or changed environment settings;
- verification performed; and
- upgrade or rollback considerations.

Review the pull request diff and wait for its checks:

```sh
gh pr view --web
gh pr checks --watch
```

Merge using a merge commit, matching the repository's existing history:

```sh
gh pr merge --merge --delete-branch
```

Do not tag the feature-branch commit. The release tag must identify the commit
on `main` that includes the merged pull request.

## Synchronize the local repository

After the pull request is merged:

```sh
git switch main
git pull --ff-only origin main
git fetch --prune --tags
git status --short --branch
```

If the local feature branch still exists and Git confirms it is fully merged,
remove it:

```sh
git branch -d FeatureName
```

Before tagging, verify the version and recent history:

```sh
cat VERSION
git log -3 --oneline --decorate
git status --porcelain
```

`git status --porcelain` must produce no output.

## Create and push the tag

Create an annotated tag on the merged `main` commit. The tag version must match
`VERSION` exactly, with only the added `v` prefix:

```sh
git tag -a v0.9.0 -m "Lemmy Vote Viewer v0.9.0"
git show --no-patch --decorate v0.9.0
```

Check that the tag's commit is contained in `main`:

```sh
git branch --contains v0.9.0
```

Push only after those checks pass:

```sh
git push origin v0.9.0
```

Never move or reuse a published release tag. If a released version is faulty,
fix it in a new patch release.

## Publish the GitHub release

For standard automatically generated notes:

```sh
gh release create v0.9.0 \
  --verify-tag \
  --title "Lemmy Vote Viewer v0.9.0" \
  --generate-notes
```

For curated notes, write Markdown to a temporary file and pass the file to
GitHub. Do not pass a quoted string containing `\n` characters:

```sh
release_notes_file=$(mktemp)
${EDITOR:-vi} "$release_notes_file"

gh release create v0.9.0 \
  --verify-tag \
  --title "Lemmy Vote Viewer v0.9.0" \
  --notes-file "$release_notes_file"

rm "$release_notes_file"
```

Curated notes should normally contain:

```markdown
## What's new

- User-visible change.
- Another user-visible change.

## Operator notes

- New configuration, database grant, compatibility, or deployment action.

## Verification

- Unit and integration checks performed.

See pull request #NN for implementation details.
```

If no operator action is required, say so explicitly. Do not publish passwords,
tokens, private hostnames, database contents, or sensitive log output.

## Verify the published release

Inspect the release and tag after publishing:

```sh
gh release view v0.9.0
git ls-remote --tags origin v0.9.0
git show --no-patch --decorate v0.9.0
```

Confirm on GitHub that:

- the release title and Markdown render correctly;
- the tag points to the intended merged commit on `main`;
- the release is marked as the latest release when appropriate;
- required operator actions are prominent; and
- linked pull requests and documentation resolve correctly.

Production deployment can then follow the tagged-deployment instructions in
the README.

## Correcting release notes

Release notes can be edited without changing the tag or application version.
Prepare a corrected Markdown file and run:

```sh
gh release edit v0.9.0 --notes-file /path/to/release-notes.md
```

Use `gh release view v0.9.0` and the GitHub page to verify the result.

Do not delete or recreate a published tag merely to correct wording. Deleting
a release or remote tag can disrupt operators and should be reserved for an
unpublished mistake that no one else could have consumed.

## Release checklist

- [ ] Choose the version using the project policy.
- [ ] Update `VERSION` without a `v` prefix.
- [ ] Update recommended tag examples when appropriate.
- [ ] Run formatting, compilation, and unit tests.
- [ ] Run database and UI checks appropriate to the change.
- [ ] Review, commit, and push only the intended files.
- [ ] Create the pull request with correctly rendered Markdown.
- [ ] Merge the pull request into `main`.
- [ ] Synchronize and verify the local `main` branch is clean.
- [ ] Create an annotated tag on the merged commit.
- [ ] Push the tag.
- [ ] Publish and inspect the GitHub release.
- [ ] Deploy from the tag using the README procedure.
