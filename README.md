<div align="center">

# 🔐 SST - Secure Shell over Telegram

### A real server terminal, driven from Telegram

[![Telegram](https://img.shields.io/badge/Telegram-@AssA__wza-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/AssA_wza)
[![GitHub](https://img.shields.io/badge/GitHub-AssA7778-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/AssA7778/SST)
[![Version](https://img.shields.io/badge/version-2.1.0-success?style=for-the-badge)](https://github.com/AssA7778/SST)

**English** · [فارسی](README.fa.md)

</div>

---

## ⚠️ Read this first

SST connects a **real root shell on your server** to a Telegram chat. That means:

> **Anyone who controls your Telegram account controls your server.**

Before you install:

- ✅ Turn on **Two-Step Verification** in Telegram (Settings → Privacy and Security)
- ✅ For servers that genuinely matter, use SSH with keys instead
- ✅ Never copy, paste or upload `config.json` - it holds your bot token and webhook secret
- ✅ Understand that Telegram bot chats are **not end-to-end encrypted** (see [Security](#-security))

This is a convenience tool, not a replacement for hardened SSH.

---

## ✨ Features

- 🖥️ **A real PTY** - `sudo`, `nano`, `htop`, pipes, job control, all of it
- 📺 **Full terminal emulation** - colours, progress bars, `clear`, and full-screen apps render correctly
- ⚡ **Live output** - the message updates as the command runs
- 🔘 **Control buttons** - Enter, Ctrl+C, Tab, arrows, Ctrl+Z, Ctrl+D, Refresh, Clear
- ⏱️ **Long commands don't get cut off** - completion is read from the PTY's foreground process group, not guessed from silence
- 📱 **Mini App** - a full terminal and file browser inside Telegram
- 🔼 **Scrollback paging** - step back through 4000 lines of history in the chat
- 📄 **`/full`** sends the entire transcript as a file
- 🔒 **Four independent authentication layers** on the webhook
- 📦 One-command install plus an `sst` management CLI
- 🇮🇷 **Persian interface** - every message, guide and command description in the chat is in Persian
- 🐍 **Zero third-party dependencies** - Python 3.8+ standard library only

---

## 🚀 Install

```bash
bash <(curl -Ls https://raw.githubusercontent.com/AssA7778/SST/main/setup.sh)
```

> Requires **root**.

The installer asks for your bot token, your numeric user ID and a connection mode, then does everything else: generates secrets, obtains a TLS certificate, installs a systemd service and registers the webhook.

| What you need | Where to get it |
|---|---|
| Bot token | [@BotFather](https://t.me/BotFather) → `/newbot` |
| Your numeric user ID | [@userinfobot](https://t.me/userinfobot) |

### Choosing a mode

| | Webhook | Polling |
|---|---|---|
| Latency | Instant | ~1 second |
| Needs a domain | ✅ Yes | ❌ No |
| Needs an open port | ✅ Yes (88 or 443) | ❌ No |
| Attack surface | One port exposed to the internet | **None** - outbound only |

> 💡 **If you don't have a domain, or you're unsure, choose Polling.** It is about a second slower but opens no ports at all, which makes it the safer default.

For webhook mode, create a DNS record first:

```
Type: A     Name: bot     Value: <your server IP>     TTL: auto
```

> Cloudflare users: set the record to **DNS only** (grey cloud). With the proxy on, Telegram cannot reach port 88.

---

## 🎮 Commands

Type `/` in the chat to see the full list. The commands are registered with
Telegram and scoped to your chat only, so anyone else who finds the bot sees
nothing.

**The bot speaks Persian.** Command descriptions, the `/start` guide, status
lines and error messages are all in Persian; terminal output is of course
untouched. Guides are sent as ordinary text rather than inside a monospace
block, so Telegram lays them out right-to-left properly, and latin tokens like
`/full` are marked so they do not flip inside a Persian sentence.

The button next to the message box is the **Terminal** Mini App, not a command
menu, so the app is always one tap away.

| Command | What it does |
|---|---|
| `/start` | Open (or show) the shell session |
| `/new` | Kill the shell and start a fresh one |
| `/close` | Close the session |
| `/app` | Open the terminal Mini App |
| `/status` | Server and session info (load, memory, uptime) |
| `/full` | Send the whole transcript as a `.txt` file |
| `/clear` | Clear the screen |
| `/key <name>` | Send a raw key: `up` `down` `left` `right` `esc` `tab` `enter` `sigint` `sigtstp` `eof` `clear` |
| `/help` | Show help |
| **anything else** | Runs on the server |

### Buttons

Two rows of arrows, and the shape tells you which is which: **thin arrows type,
page arrows scroll.**

| Button | Action |
|---|---|
| ↵ ⛔ ⇥ | Enter, Ctrl+C, Tab |
| ↑ ↓ | Shell command history (the real arrow keys) |
| 🔼 🔽 | Scroll back and forward through past output |
| ⟳ | Refresh the screen, for long-running commands |
| ⌧ | Clear |
| 📄 File | Send the whole transcript as a file |

Tapping 🔼 switches the message to a scroll view with ⏫ 🔼 🔽 ⏬ and ⤓ Live.
The shell keys disappear there, so a scroll tap can never reach the shell.
Typing any command snaps straight back to live. Ctrl+Z and Ctrl+D are under
`/key`.

A chat message holds 55 lines; the scrollback keeps 4000. For anything longer,
`/full` or the Mini App is the better tool.

---

## 📱 Mini App

`/app` opens a full terminal inside Telegram: no 4096-character limit, no message
editing, real scrolling, and a file browser alongside it. It drives **the same
shell session** as the chat, so you can start a command in one and watch it in
the other.

| Tab | What it does |
|---|---|
| **TERMINAL** | Live screen over a WebSocket, on-screen key bar for ^C, Tab, Esc, arrows, ^Z, ^D, ^L |
| **FILES** | Browse any directory, preview text files, download anything, breadcrumb navigation |

It renders the screen server-side and streams the text, so there is no xterm.js
and no CDN to reach - the whole page is one self-contained file, which matters
on networks where CDNs are slow or blocked.

**Requirements:** webhook mode, a domain, and a CA-signed certificate.
Telegram's WebView refuses self-signed certificates, so the button only appears
when it can actually work. `/app` explains why if it cannot.

Authentication is Telegram's signed `initData`: the payload is HMAC-SHA256'd
under a key derived from your bot token, and SST checks the signature, the user
id and the age before issuing a short-lived session token. `/app` also prints a
plain-browser link protected by a separate key, for when you want the terminal
on a laptop.

---

## ⚙️ Management

```bash
sst status      # service status
sst logs        # follow the log
sst restart     # restart
sst config      # edit config.json, then restart automatically
sst webhook     # what Telegram thinks of your webhook
sst update      # pull the latest version and restart
sst uninstall   # remove completely
```

---

## 🔒 Security

### How your data travels

```
   Your phone
       │  MTProto  (Telegram's client↔server encryption)
       ▼
 Telegram servers  ←── can read your commands and their output
       │
       ├─ webhook mode:  TLS 1.2+  ──►  your server :88
       │
       └─ polling mode:  your server  ──TLS 1.2+──►  api.telegram.org
                         (certificate and hostname verified)
```

**Bot chats are not end-to-end encrypted.** Telegram's secret chats do not apply to bots, so every command you type and every byte of output passes through Telegram's servers in a form they can read. This is how the Bot API works; no bot can avoid it.

**Do not use SST to handle secrets** - don't `cat` a private key, don't type database passwords, don't `env` a production box. Use SSH for that.

### Transport encryption

| Direction | Protection |
|---|---|
| Telegram → your server (webhook) | TLS 1.2 minimum (TLS 1.0/1.1 refused), Let's Encrypt certificate, auto-renewed nightly |
| Your server → Telegram (polling) | TLS with full certificate-chain and hostname verification |
| Self-signed fallback | RSA-2048 / SHA-256, public certificate uploaded to Telegram via `setWebhook` so it is actually trusted |

### The four authentication layers

In webhook mode your server has a port open to the internet. For that port not to be a free shell for the whole internet, **every** request must pass all four:

| # | Layer | Detail |
|---|---|---|
| 1 | **Source IP** | Must be inside Telegram's published ranges `149.154.160.0/20` and `91.108.4.0/22` |
| 2 | **Secret URL path** | The webhook lives at `/<32 random hex chars>` - 128 bits of entropy, not `/` |
| 3 | **`secret_token`** | The `X-Telegram-Bot-Api-Secret-Token` header must match a 64-character secret (256 bits), compared with `hmac.compare_digest` so timing reveals nothing |
| 4 | **Identity binding** | The update must come from your `user_id`, **in your own private chat** (`chat.id == user_id`, type `private`) |

Anything that fails gets a bare **404** - not a 403, not an error message, and no server banner. A scanner learns nothing about what is behind the port.

Layer 4 matters even if the others fall: an attacker who somehow obtained the secret still cannot redirect output to their own chat, because `chat_id` is checked against your own ID rather than taken from the request.

The bot **refuses to start** in webhook mode without a secret and a secret path. There is no "fall back to trusting the request body" path - that was exactly the flaw in 1.x.

### At rest

| Path | Mode |
|---|---|
| `/opt/sst` | `0700` |
| `/opt/sst/config.json` | `0600` - bot token + webhook secret |
| `/opt/sst/certs/*.pem` | `0600` |
| systemd unit | `UMask=0077` |

SST warns loudly at startup if `config.json` is readable by anyone else. Secrets are **never written to the log** - the registered webhook URL is logged with the path redacted.

### Other hardening

- Request bodies are capped at 2 MB; malformed JSON is rejected before parsing anything meaningful
- The HTTP server reports itself as `nginx` and never exposes a Python version
- Telegram receives its `200 OK` *before* the command runs, so a slow command can never trigger a webhook retry and run twice
- `drop_pending_updates` on start - commands queued while the bot was down are discarded rather than replayed
- Idle sessions are reaped after 30 minutes by default
- Closing a session sweeps the **entire process session**, so background and `disown`ed jobs cannot outlive it
- Every rejected request is counted and logged; `/status` shows the total

### What SST does *not* protect against

- **Your Telegram account being compromised** - that is full server access, and 2FA is your only defence
- **`config.json` leaking** - anyone with the token can impersonate your bot
- **Telegram itself** - see the note about end-to-end encryption above
- **Yourself** - the bot runs as root on purpose; there is no command allowlist

If you want to reduce the blast radius, run the service as a non-root user and grant specific commands through `sudoers` - change `User=` in `/etc/systemd/system/sst-bot.service`.

---

## 🧩 How it works

```
Telegram ──► webhook / polling ──► auth layers ──► handle_update()
                                                        │
                                                        ▼
                                              Session (one per user)
                                       ┌────────────────┴────────────────┐
                                       │  bash --login on a real PTY     │
                                       │  own session + controlling tty  │
                                       └────────────────┬────────────────┘
                                                        │ one reader thread
                                                        ▼
                                        terminal.py - VT100 screen model
                                                        │
                                                        ▼
                                       throttled editMessageText updates
```

Two notes on the design:

`terminal.py` is a screen emulator rather than an ANSI stripper. It models a character grid with a cursor, scroll regions, an alternate screen and scrollback, so `top` and `nano` produce a correct picture rather than a wall of escape codes. It is pure standard library.

Command completion is read rather than guessed. `os.tcgetpgrp()` on the PTY master tells us whether the shell or a child holds the terminal. When the shell has it back - continuously, so loops that hand it back and forth don't fool us - the command is genuinely finished. This is why a 20-minute build keeps streaming instead of being declared done after two quiet seconds.

---

## ⚙️ Advanced configuration (`config.json`)

| Key | Default | Meaning |
|---|---|---|
| `cols` / `rows` | `80` / `55` | Terminal dimensions. 55 rows fills a Telegram message; much beyond 60 gets truncated |
| `session_timeout` | `1800` | Seconds of idleness before the shell is closed (`0` = never) |
| `edit_interval` | `1.2` | Seconds between message edits - lower risks Telegram rate limits |
| `max_stream` | `300` | Maximum seconds to stream one command before showing "still running" |
| `idle_stop` | `2.0` | Silence fallback, used only if the PTY pgid cannot be read |
| `ip_filter` | `true` | Telegram IP range check - set `false` if you sit behind a reverse proxy |
| `tls` | `true` | Set `false` if nginx/Caddy terminates TLS in front of you |
| `bind` | `0.0.0.0` | Listen address |
| `shell` | `["bash","--login"]` | The shell to run |
| `miniapp` | `true` | Serve the Mini App |
| `webapp_key` | generated | Key for the plain-browser link |
| `log_level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Apply changes with `sst restart`.

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---|---|
| Bot doesn't respond | `sst logs` - `rejected` lines mean authentication is failing |
| `sst webhook` shows an SSL error | Self-signed certificate and your domain doesn't resolve to this server |
| Telegram can't reach the port | Set Cloudflare to DNS-only; check `ss -lntp \| grep 88` and your firewall |
| A command seems to stop early | Tap ⟳ Refresh - the shell is still running |
| Output looks wrapped or cramped | `sst config` → adjust `cols` / `rows` |
| Only the last lines of a long list show | Tap 🔼 Scroll back, or use `/full` / the Mini App |
| The Mini App button is missing | You need webhook mode with a CA-signed certificate; send `/app` for the reason |
| Mini App opens blank | Self-signed certificate, or the port is not reachable from your phone |
| Service won't start after reboot | `journalctl -u sst-bot -n 50 --no-pager` |
| Rate-limit warnings in the log | Raise `edit_interval` |

---

## 📂 Layout

```
SST/
├── bot.py          # bot, webhook server, session management
├── terminal.py     # VT100 screen emulator
├── miniapp.py      # initData auth, WebSocket, file API
├── webapp.py       # Mini App page
├── setup.sh        # installer / uninstaller
├── config.json     # created at install time (0600)
└── certs/          # created at install time (0600)
```

---

## 📜 License

MIT - see [LICENSE](LICENSE). Changes are documented in [CHANGELOG.md](CHANGELOG.md).

<div align="center">

**Built by [AssA7778](https://github.com/AssA7778)** · [@AssA_wza](https://t.me/AssA_wza)

</div>
