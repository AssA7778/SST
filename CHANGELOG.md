# Changelog

## 2.1.0

### 🔴 Security

- **Fixed remote code execution as root (critical).** In 1.x the webhook handler
  read the sender's identity out of the request body it was supposed to be
  authenticating:

  ```python
  uid = msg["from"]["id"]
  if uid != ALLOWED_USER: ...      # attacker-controlled value
  ```

  Since the server listened on `0.0.0.0:88` with no verification that a request
  came from Telegram at all, anyone on the internet could POST a forged update
  with the owner's user ID and their own `chat_id`, and receive the output of
  any command in their own chat. The only barrier was guessing a Telegram user
  ID, which is not a secret.

  Requests now have to pass four independent checks:

  1. source IP inside Telegram's published ranges (`149.154.160.0/20`, `91.108.4.0/22`)
  2. a 32-character random URL path
  3. `X-Telegram-Bot-Api-Secret-Token` matching a 64-character secret (constant-time)
  4. the update must come from the configured user, in that user's own private chat

  Everything rejected gets a bare `404` - no version banner, no error detail.
- The bot refuses to start in webhook mode without a webhook secret and secret
  path, instead of silently falling back to trusting the request body.
- `config.json` is now `0600` and `/opt/sst` is `0700`. In 1.x the file was
  created with the default `0644`, leaving the bot token readable by every
  local user. The bot warns at startup if the permissions are wrong.
- Values collected by the installer are passed to the config writer as argv
  rather than interpolated into a script body.
- systemd unit sets `UMask=0077`.

### 🐛 Fixed

- **Polling mode did nothing.** `threading.Thread(target=H.__dict__["do_POST"],
  args=(None, None))` called an unbound method with the wrong arity and never
  looked at the update it had just fetched, so every message in polling mode
  was silently discarded. Both transports now share one `handle_update()`.
- **Self-signed certificates never worked.** Telegram only accepts one if the
  public certificate is uploaded with `setWebhook` as multipart; 1.x set the
  webhook with a plain GET, so that fallback always failed. The bot now
  registers its own webhook and uploads the certificate when needed.
- **Output containing a backtick broke every message.** `parse_mode: Markdown`
  around un-escaped terminal output made Telegram reject the edit, so the
  message froze. Now `parse_mode: HTML` with proper escaping inside `<pre>`.
- **Commands were cut off after ~15 seconds** (or 1.5s of silence). Completion
  is now detected from the PTY's foreground process group, which is
  authoritative; long builds keep streaming, and ⟳ Refresh picks up the rest.
- **Two threads read the same PTY concurrently**, interleaving and losing
  output. A single dedicated reader thread per session now owns the fd.
- **Rate limiting.** 1.x could issue ~50 `editMessageText` calls per command
  (one every 0.3s); Telegram allows roughly one message per second per chat.
  Edits are throttled and `429` responses are honoured with `retry_after`.
- **Dead shells were never noticed** - `get_pty` returned the same dead object
  forever. Sessions are health-checked and recycled.
- **Background jobs outlived the session.** Under job control each `&` job gets
  its own process group, so killing the shell's group left `nohup ... &`
  processes running forever. Teardown now sweeps the entire session.
- Re-running the installer appended a duplicate `crontab` line every time. The
  renewal job is a `/etc/cron.d` drop-in, which is idempotent by construction.
- `setup.sh` had no shebang and no `set -e`.
- Removed the operator-precedence accident in `dedup_lines`
  (`a and b if c else d`), along with the whole function.
- Replaced every bare `except:` (which also swallowed `KeyboardInterrupt`).
- `HTTPServer` → `ThreadingHTTPServer`; webhook replies are sent before the
  command runs, so Telegram never retries and duplicates a command.

### ✨ Added

- `terminal.py`: a real VT100/xterm screen emulator replacing the regex ANSI
  stripping. `top`, `htop`, `nano`, `clear`, progress bars, alternate screen
  and scrollback all render correctly. Still zero third-party dependencies.
  (The old `strip_ansi` also deleted the literal string `(B` from anywhere in
  the output, and `re.sub(r"\x1b.", "", s)` ate real characters.)
- `/status`, `/full`, `/clear`, `/key`, `/help` commands.
- The commands are registered with `setMyCommands` so they show up in
  Telegram's / menu, scoped to the owner's chat so nobody else sees them.
- **Mini App** (`/app`): a full terminal plus a file browser inside Telegram,
  driving the same shell session as the chat. Screen rendering stays on the
  server and the page streams plain text over a WebSocket, so there is no
  xterm.js and no CDN dependency. Authenticated with Telegram's signed
  `initData` (HMAC-SHA256 over the bot token), with the user id and signature
  age both checked, plus a separate key for plain-browser access.
  The RFC 6455 WebSocket server is hand-written to keep the dependency count
  at zero.
- **Scrollback paging in chat.** The emulator already kept 4000 lines; the
  keyboard now exposes them. Thin arrows (↑ ↓) are the shell's own arrow keys,
  page arrows (🔼 🔽) move through history, and tapping one switches the message
  to a scroll layout with no shell keys at all. Typing a command returns to the
  live view.
- Default screen grew from 60x30 to 80x55. A Telegram message holds 4096
  characters and the old default used under half of it.
- The chat menu button now launches the Mini App directly. Commands stay
  registered and appear when you type `/`.
- `/start` prints a real orientation guide instead of one line.
- **The bot's own text is now Persian**: command descriptions in the `/` list,
  the start guide, `/help`, `/status`, status lines and error messages. Terminal
  output is untouched. Guides moved out of the `<pre>` frame, because Persian
  inside a monospace block fights the grid; latin tokens carry LRM marks so
  `/new` does not render as `new/` mid-sentence, and values are only pinned
  left-to-right when they are actually ASCII.
- ⟳ Refresh, ⌧ Clear and 📄 Full output buttons.
- `sst` management CLI: `status`, `logs`, `restart`, `config`, `webhook`,
  `update`, `uninstall`.
- `setup.sh uninstall`, which also deletes the webhook at Telegram.
- Idle sessions are reaped after `session_timeout` (default 30 min).
- Input validation in the installer with re-prompting.
- Configurable `cols`, `rows`, `tls`, `ip_filter`, `shell`, `api_base`.

### Verification

Both transports, the terminal emulator and the installer were tested against a
mock Bot API before release, including the 1.x forged-update request as a
regression case.

### ⚠️ Upgrading from 1.x

The config format changed. The cleanest path:

```bash
bash <(curl -Ls https://raw.githubusercontent.com/AssA7778/SST/main/setup.sh) uninstall
bash <(curl -Ls https://raw.githubusercontent.com/AssA7778/SST/main/setup.sh)
```

`{"polling": true}` is still understood and maps to `"mode": "polling"`, but a
1.x config has no `webhook_secret`, so webhook mode will refuse to start until
you reinstall or add one manually (`openssl rand -hex 32`).

**If you ran 1.x in webhook mode on a public IP, assume the server was
reachable.** Rotate the bot token via @BotFather, check `~/.ssh/authorized_keys`,
`crontab -l`, and `last`.
