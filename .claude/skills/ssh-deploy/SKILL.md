---
name: ssh-deploy
description: Connect to the deployment server over SSH to inspect or operate the running Docker Compose stack. Use when asked to check the server, look at running containers, view logs, or run remote commands on the host.
---

# SSH to the deployment server

The app runs on a remote host as a Docker Compose stack. Use this skill whenever a
task requires running commands on that server (checking containers, logs, health,
restarting services, etc.).

The SSH host alias is configured in the user's `~/.ssh/config`; refer to it by its
alias rather than hardcoding an address.

## Network sandbox: SSH must go through the SOCKS5 proxy

This environment runs in a restricted network namespace with **no default route** —
a direct `ssh <host>` fails with `Network is unreachable` or `connect: Network is
unreachable`. Outbound traffic only works through the SOCKS5 proxy that listens on
`127.0.0.1:1080` (an HTTP proxy is also available on `127.0.0.1:1081`).

`--dangerously-disable-sandbox` does **not** help — the restriction is on the
namespace, not on Bash permissions.

`nc` (OpenBSD netcat) is usually **not** installed; use `ncat` (from nmap) for the
SOCKS5 `ProxyCommand`.

### Running remote commands

Run commands directly over SSH through the proxy:

```bash
ssh -o ProxyCommand='ncat --proxy 127.0.0.1:1080 --proxy-type socks5 %h %p' <host> 'docker ps'
```

Optionally, drive remote work inside a tmux session so the user can
`tmux attach -t <session>` and follow along live — useful for long or interactive
work. Not required; use it when it helps.

```bash
tmux kill-session -t <session> 2>/dev/null
tmux new-session -d -s <session> -x 220 -y 50 \
  "ssh -o ProxyCommand='ncat --proxy 127.0.0.1:1080 --proxy-type socks5 %h %p' <host>"
tmux send-keys -t <session> 'docker compose ls' Enter
sleep 3
tmux capture-pane -t <session> -p -S -40
```

### Gotchas

- **Avoid `sudo`** — it blocks on an interactive password prompt and hangs. Prefer
  non-sudo reads (e.g. read nginx config straight from `/etc/nginx/sites-enabled/`
  instead of `sudo nginx -T`).
- When using tmux: give SSH a few seconds before the first `send-keys`; capture the
  raw pane (`capture-pane -p -S -N`) without aggressive `grep`/`sed` filtering; send
  `clear` between steps. If the user is typing in the same session, `send-keys`
  appends to their line — send `C-u` first, and never press Enter on half-typed text
  that isn't yours.

### Copying files to the server (scp through the proxy)

To deploy a script/config, write it locally and `scp` it — far more reliable than
heredoc-ing a multi-line file through `send-keys`. The same `ProxyCommand` is
required:

```bash
scp -o ProxyCommand='ncat --proxy 127.0.0.1:1080 --proxy-type socks5 %h %p' \
  ./local_file.sh <host>:/home/<user>/remote_file.sh
```

Then `chmod +x` and test it.

## Operating the stack

The Compose project lives in the user's home directory on the server. Run
`docker compose` commands from that directory. Common operations:

```bash
docker compose ls                          # list compose projects + status
docker ps                                  # running containers
docker logs --tail 100 <container>         # app logs
docker inspect --format '{{json .State.Health}}' <container> | python3 -m json.tool
docker compose up -d                       # apply compose changes
```

### Healthcheck note

The app image ships with `python` but **not** `curl`/`wget`. A healthcheck that
shells out to `curl` will always fail (`exec: "curl": executable file not found`)
even when the app is healthy. Probe `/health` from inside the container with Python
instead:

```bash
docker exec <container> python -c \
  "import urllib.request as u; print(u.urlopen('http://localhost:8000/health').read().decode())"
```
