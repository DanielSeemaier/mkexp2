# Deployment

This repo's web UI deployment runs on the KIT login node as a detached tmux
session.

## Target

- Host: `seemaier@login.ae.iti.kit.edu`
- Server checkout: `~/mkexp2`
- Experiment repo served by the UI: `~/i10-experiments`
- tmux session: `mkexp2-web`
- Bind address: `127.0.0.1:8765`
- Public host hint for share links: `login.ae.iti.kit.edu`

The web app is tunnel-only. From a laptop, connect with:

```zsh
ssh -L 8765:127.0.0.1:8765 seemaier@login.ae.iti.kit.edu
```

Then open `http://127.0.0.1:8765`.

## Redeploy Rule

After each completed Codex prompt in this repo, push the finished work to
`origin/main` and redeploy this tmux session. Do not hotpatch files directly on
the login node.

## Redeploy Command

Run this from the local checkout after committing and pushing `main`:

```zsh
ssh seemaier@login.ae.iti.kit.edu 'zsh -s' <<'REMOTE'
set -e
cd ~/mkexp2
git fetch origin main
git checkout main
git pull --ff-only origin main
zsh -ic 'tmux kill-session -t mkexp2-web 2>/dev/null || true; tmux new-session -d -s mkexp2-web "cd ~/mkexp2 && MKEXP2_WEB_PUBLIC_HOST=login.ae.iti.kit.edu ./bin/mkexp2 web --repo ~/i10-experiments --host 127.0.0.1 --port 8765"'
tmux ls | grep mkexp2-web
REMOTE
```

Starting tmux through interactive zsh is intentional: the login node shell
initialization loads Spack and other environment setup needed by web-triggered
commands.
