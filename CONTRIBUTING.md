# Contributing

Every change starts as an RFC.

## 1. Open an RFC

Before writing code, [open an issue](https://github.com/huggingface/safeagents/issues/new) titled `RFC: <short summary>` that covers:

- **Problem**: what is missing or broken, and who it affects.
- **Proposal**: what you want to change, and roughly how.
- **Alternatives**: other options you considered, if any.

Maintainers will discuss it with you and either accept or decline it.

## 2. Push a branch once the RFC is accepted

Only collaborators can open pull requests on this repo. Once your RFC is accepted:

1. Implement it on a branch of your fork.
2. Comment on the RFC issue with a link to the branch.
3. A maintainer opens the PR from your branch. Your commits keep your authorship, and review happens on the PR as usual. Keep pushing to the same branch to address feedback.

Small fixes (typos, broken links, obvious one-line bugs) don't need the RFC discussion: open an issue describing the fix and link your branch.

Before pushing, make sure tests and formatting pass:

```
uv sync
uv run pytest
uvx ruff format .
```
