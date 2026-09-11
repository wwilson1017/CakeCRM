<!--
Thanks for contributing to CakeCRM! Please fill out the sections below.
Keep the PR focused — one logical change per PR.
-->

## Summary

<!-- What does this PR do, and why? Link the issue it closes: "Closes #123". -->

## Test plan

<!-- How you verified the change. Include the commands you ran. -->

- [ ] Backend: `ruff check .` and `python -m pytest -q` pass (from `backend/`)
- [ ] Frontend (if touched): `npm run build` and `npm run lint` pass (from `frontend/`)

## Checklist

- [ ] I signed off all my commits (`git commit -s`) per the [DCO](https://github.com/wwilson1017/CakeCRM/blob/main/CONTRIBUTING.md#developer-certificate-of-origin-dco).
- [ ] No secrets, API keys, or real customer data are included in this PR.
- [ ] I did not add an email-send tool or widen Gmail scopes beyond read + create-draft (see [AGENTS.md](https://github.com/wwilson1017/CakeCRM/blob/main/AGENTS.md)).
