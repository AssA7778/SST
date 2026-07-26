"""SST - Secure Shell over Telegram."""

import codecs
import fcntl
import hmac
import html
import io
import ipaddress
import json
import logging
import mimetypes
import os
import pty
import re
import secrets
import select
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import miniapp
import webapp
from terminal import Terminal

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("SST_CONFIG", os.path.join(BASE_DIR, "config.json"))
VERSION = "2.1.0"

TELEGRAM_NETS = [
    ipaddress.ip_network("149.154.160.0/20"),
    ipaddress.ip_network("91.108.4.0/22"),
]

log = logging.getLogger("sst")


DEFAULTS = {
    "server_name": "Server",
    "ssh_user": "root",
    "ssh_host": "",
    "port": 88,
    "mode": "webhook",
    "webhook_domain": "",
    "webhook_secret": "",
    "webhook_path": "",
    "tls": True,
    "cert": "certs/cert.pem",
    "key": "certs/key.pem",
    "upload_certificate": False,
    "ip_filter": True,
    "bind": "0.0.0.0",
    "cols": 80,
    "rows": 55,
    "shell": ["bash", "--login"],
    "session_timeout": 1800,
    "edit_interval": 1.2,
    "idle_stop": 2.0,
    "max_stream": 300,
    "api_base": "https://api.telegram.org",
    "log_level": "INFO",
    "miniapp": True,
    "webapp_key": "",
}


class ConfigError(Exception):
    pass


def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        raise ConfigError(f"config not found: {path}\nRun setup.sh first.")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json is not valid JSON: {exc}") from exc

    cfg = dict(DEFAULTS)
    cfg.update(raw)

    if "polling" in raw and "mode" not in raw:
        cfg["mode"] = "polling" if raw["polling"] else "webhook"

    if not cfg.get("bot_token"):
        raise ConfigError("config.json: 'bot_token' is required")
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", str(cfg["bot_token"])):
        raise ConfigError("config.json: 'bot_token' does not look like a bot token")
    try:
        cfg["user_id"] = int(cfg["user_id"])
    except (KeyError, TypeError, ValueError):
        raise ConfigError("config.json: 'user_id' must be your numeric Telegram ID")
    if cfg["user_id"] <= 0:
        raise ConfigError("config.json: 'user_id' must be a positive integer")

    cfg["mode"] = str(cfg["mode"]).lower()
    if cfg["mode"] not in ("webhook", "polling"):
        raise ConfigError("config.json: 'mode' must be 'webhook' or 'polling'")

    if cfg["mode"] == "webhook":
        if not cfg["webhook_domain"]:
            raise ConfigError("config.json: 'webhook_domain' is required in webhook mode")
        if int(cfg["port"]) not in (80, 88, 443, 8443):
            raise ConfigError(
                "config.json: Telegram only delivers webhooks to ports 80, 88, 443 or 8443"
            )
        if len(str(cfg["webhook_secret"])) < 32:
            raise ConfigError(
                "config.json: 'webhook_secret' must be at least 32 characters.\n"
                "Generate one with:  openssl rand -hex 32"
            )
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,}", str(cfg["webhook_path"] or "")):
            raise ConfigError(
                "config.json: 'webhook_path' must be at least 16 URL-safe characters.\n"
                "Generate one with:  openssl rand -hex 16"
            )
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(cfg["webhook_secret"])):
            raise ConfigError(
                "config.json: 'webhook_secret' may only contain A-Z a-z 0-9 _ - "
                "(Telegram's own restriction)"
            )

    for key in ("cert", "key"):
        if cfg[key] and not os.path.isabs(cfg[key]):
            cfg[key] = os.path.join(BASE_DIR, cfg[key])

    if not cfg["ssh_host"]:
        cfg["ssh_host"] = cfg["webhook_domain"] or socket.gethostname()

    cfg["cols"] = max(20, min(200, int(cfg["cols"])))
    cfg["rows"] = max(8, min(120, int(cfg["rows"])))
    if isinstance(cfg["shell"], str):
        cfg["shell"] = [cfg["shell"]]

    return cfg


def check_permissions(path):
    """Warn if config.json is group/world readable."""
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        return
    if mode & 0o077:
        log.warning(
            "%s is mode %o. It contains your bot token and webhook secret. "
            "Fix with: chmod 600 %s", path, mode, path
        )


class Telegram:
    """Bot API client."""

    def __init__(self, token, api_base="https://api.telegram.org"):
        self._token = token
        self._base = api_base.rstrip("/") + "/bot" + token + "/"

    def _request(self, method, data, headers, timeout):
        url = self._base + method
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, {"ok": False, "description": body[:300]}

    def call(self, method, params=None, timeout=20, retries=2, quiet_errors=()):
        """Returns the result field, or None on failure."""
        payload = json.dumps(params or {}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Connection": "close"}
        for attempt in range(retries + 1):
            try:
                status, body = self._request(method, payload, headers, timeout)
            except (urllib.error.URLError, socket.timeout, OSError) as exc:
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                log.warning("telegram %s: network error: %s", method, exc)
                return None
            except json.JSONDecodeError as exc:
                log.warning("telegram %s: bad JSON: %s", method, exc)
                return None

            if body.get("ok"):
                return body.get("result")

            desc = str(body.get("description", ""))
            if status == 429:
                wait = float(body.get("parameters", {}).get("retry_after", 3))
                log.info("telegram %s: rate limited, sleeping %.1fs", method, wait)
                time.sleep(min(wait, 30) + 0.2)
                continue
            if any(token in desc for token in quiet_errors):
                return None
            log.warning("telegram %s failed (%s): %s", method, status, desc[:200])
            return None
        return None

    def upload(self, method, params, files, timeout=60):
        """Multipart call, for sendDocument and self-signed setWebhook."""
        boundary = "----SST" + secrets.token_hex(16)
        buf = io.BytesIO()

        def w(text):
            buf.write(text.encode("utf-8"))

        for key, value in (params or {}).items():
            if value is None:
                continue
            if not isinstance(value, str):
                value = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
            w(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n")
            w(value + "\r\n")

        for key, (filename, content) in (files or {}).items():
            ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            w(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"; "
              f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n")
            buf.write(content if isinstance(content, bytes) else content.encode("utf-8"))
            w("\r\n")
        w(f"--{boundary}--\r\n")

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Connection": "close",
        }
        try:
            status, body = self._request(method, buf.getvalue(), headers, timeout)
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            log.warning("telegram %s: upload failed: %s", method, exc)
            return None
        if body.get("ok"):
            return body.get("result")
        log.warning("telegram %s failed (%s): %s", method, status,
                    str(body.get("description"))[:200])
        return None

    def send_message(self, chat_id, text, keyboard=None):
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True}
        if keyboard:
            params["reply_markup"] = keyboard
        return self.call("sendMessage", params)

    def edit_message(self, chat_id, message_id, text, keyboard=None):
        params = {"chat_id": chat_id, "message_id": message_id, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True}
        if keyboard:
            params["reply_markup"] = keyboard
        return self.call("editMessageText", params,
                         quiet_errors=("message is not modified",))

    def answer_callback(self, callback_id, text=None):
        params = {"callback_query_id": callback_id}
        if text:
            params["text"] = text
        return self.call("answerCallbackQuery", params, retries=0)

    def send_document(self, chat_id, filename, content, caption=None):
        params = {"chat_id": chat_id}
        if caption:
            params["caption"] = caption
        return self.upload("sendDocument", params, {"document": (filename, content)})


_SPAWN_LOCK = threading.Lock()


class Session:
    """A shell on a PTY with a dedicated reader thread."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.term = Terminal(cfg["cols"], cfg["rows"])
        self.data_event = threading.Event()
        self.created = time.time()
        self.last_data = time.time()
        self.last_input = time.time()
        self.stream_gen = 0
        self.scroll = 0
        self.closed = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        self.master, slave = pty.openpty()
        slave_name = os.ttyname(slave)
        self._set_winsize()

        env = dict(os.environ)
        env.update({
            "TERM": "xterm-256color",
            "LC_ALL": env.get("LC_ALL", "C.UTF-8"),
            "LANG": env.get("LANG", "C.UTF-8"),
            "COLUMNS": str(cfg["cols"]),
            "LINES": str(cfg["rows"]),
            "SST": "1",
        })

        def preexec():
            os.setsid()
            fd = os.open(slave_name, os.O_RDWR)
            try:
                fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
            except OSError:
                pass
            os.close(fd)

        try:
            with _SPAWN_LOCK:
                self.proc = subprocess.Popen(
                    cfg["shell"],
                    stdin=slave, stdout=slave, stderr=slave,
                    env=env, close_fds=True, preexec_fn=preexec,
                    cwd=env.get("HOME") or "/",
                )
        finally:
            os.close(slave)

        self.reader = threading.Thread(target=self._read_loop, daemon=True,
                                       name="pty-reader")
        self.reader.start()
        self._drain_banner()

    def _set_winsize(self):
        try:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ,
                        struct.pack("HHHH", self.cfg["rows"], self.cfg["cols"], 0, 0))
        except OSError:
            pass

    def _drain_banner(self):
        """Swallow the login banner, extending while output still arrives."""
        start = time.time()
        cap = start + 3.0
        deadline = start + 0.6
        while time.time() < min(deadline, cap):
            if self.data_event.wait(0.1):
                self.data_event.clear()
                deadline = time.time() + 0.3
        with self.lock:
            self.term.reset()

    def _read_loop(self):
        while not self.closed:
            try:
                ready, _, _ = select.select([self.master], [], [], 0.4)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            text = self._decoder.decode(chunk)
            if not text:
                continue
            with self.lock:
                self.term.feed(text)
                self.last_data = time.time()
            self.data_event.set()
        self.closed = True
        self.data_event.set()

    @property
    def alive(self):
        return not self.closed and self.proc.poll() is None

    def busy(self):
        """True while a foreground command holds the terminal.

        The pgid equals the shell's own pid only when it is at the prompt.
        None means undeterminable; callers fall back to the silence heuristic.
        """
        if self.closed:
            return False
        try:
            return os.tcgetpgrp(self.master) != self.proc.pid
        except (OSError, AttributeError):
            return None

    def _signal_group(self, sig):
        try:
            os.killpg(self.proc.pid, sig)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self.proc.send_signal(sig)
                return True
            except (ProcessLookupError, OSError):
                return False

    def _session_pids(self):
        """Every pid in the shell's session.

        Job control puts each background job in its own process group, so a
        killpg on the shell misses them. The session id is what they share.
        """
        sid = self.proc.pid
        me = os.getpid()
        found = []
        try:
            entries = os.listdir("/proc")
        except OSError:
            return found
        for entry in entries:
            if not entry.isdigit():
                continue
            pid = int(entry)
            if pid == me or pid == sid:
                continue
            try:
                with open(f"/proc/{entry}/stat", "rb") as fh:
                    data = fh.read()
                fields = data[data.rindex(b")") + 2:].split()
                if int(fields[3]) == sid:
                    found.append(pid)
            except (OSError, ValueError, IndexError):
                continue
        return found

    def close(self):
        """Tear down the shell and everything it started.

        Interactive bash ignores SIGTERM, and killing it alone orphans its jobs.
        """
        if self.closed:
            return
        self.closed = True

        self._signal_group(signal.SIGHUP)
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._signal_group(signal.SIGKILL)
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                log.warning("shell pid %s would not die", self.proc.pid)
        except Exception:
            pass

        strays = self._session_pids()
        for pid in strays:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        if strays:
            log.info("cleaned up %d leftover process(es) from the session",
                     len(strays))
        self._signal_group(signal.SIGKILL)

        try:
            os.close(self.master)
        except OSError:
            pass
        self.data_event.set()

    def write(self, data):
        if not self.alive:
            raise BrokenPipeError("session is not running")
        self.last_input = time.time()
        payload = data.encode("utf-8")
        while payload:
            try:
                written = os.write(self.master, payload)
            except OSError as exc:
                raise BrokenPipeError(str(exc)) from exc
            payload = payload[written:]

    def bump_stream(self):
        """Invalidate any in-flight streamer, return a new generation id."""
        with self.lock:
            self.stream_gen += 1
            return self.stream_gen

    def render(self):
        with self.lock:
            return self.term.text()

    def view(self):
        """The visible window plus where it sits in the history."""
        with self.lock:
            if self.scroll <= 0:
                text = self.term.text()
                total = len(self.term.history())
                return text, 0, total
            text, offset, total = self.term.page(self.scroll)
            self.scroll = offset
            return text, offset, total

    def page_by(self, delta):
        with self.lock:
            step = max(1, self.term.rows - 3) * delta
            _, offset, _ = self.term.page(max(0, self.scroll + step))
            self.scroll = offset
            return offset

    def scroll_to_top(self):
        with self.lock:
            _, offset, _ = self.term.page(10 ** 9)
            self.scroll = offset
            return offset

    def scroll_live(self):
        with self.lock:
            self.scroll = 0

    def transcript(self):
        with self.lock:
            return self.term.full_text()

    def clear(self):
        with self.lock:
            self.term.reset()

    def resize(self, cols, rows):
        cols = max(40, min(200, int(cols or 0) or self.cfg["cols"]))
        rows = max(10, min(120, int(rows or 0) or self.cfg["rows"]))
        with self.lock:
            if (cols, rows) == (self.term.cols, self.term.rows):
                return
            self.term.resize(cols, rows)
        try:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
            os.killpg(self.proc.pid, signal.SIGWINCH)
        except OSError:
            pass
        self.data_event.set()

    def restore_size(self):
        self.resize(self.cfg["cols"], self.cfg["rows"])


class SessionManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self._sessions = {}
        self._lock = threading.Lock()

    def get(self, uid, create=True):
        with self._lock:
            session = self._sessions.get(uid)
            if session is not None and not session.alive:
                log.info("session for %s died, recycling", uid)
                session.close()
                session = None
                self._sessions.pop(uid, None)
            if session is None and create:
                session = Session(self.cfg)
                self._sessions[uid] = session
                log.info("started shell session for %s (pid %s)", uid, session.proc.pid)
            return session

    def kill(self, uid):
        with self._lock:
            session = self._sessions.pop(uid, None)
        if session:
            session.close()

    def shutdown(self):
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()

    def reap_idle(self):
        timeout = float(self.cfg["session_timeout"] or 0)
        if timeout <= 0:
            return
        now = time.time()
        with self._lock:
            stale = [uid for uid, s in self._sessions.items()
                     if not s.alive or now - max(s.last_input, s.last_data) > timeout]
            victims = [self._sessions.pop(uid) for uid in stale]
        for uid, session in zip(stale, victims):
            log.info("reaping idle session for %s", uid)
            session.close()


KEY_MAP = {
    "enter": "\r",
    "sigint": "\x03",
    "tab": "\t",
    "sigtstp": "\x1a",
    "eof": "\x04",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "left": "\x1b[D",
    "right": "\x1b[C",
    "esc": "\x1b",
    "clear": "\x0c",
}

MAX_BODY = 3000
TG_LIMIT = 4096


def build_keyboard(scrolling=False, webapp_url=None):
    """Two layouts. Plain arrows drive the shell, page arrows move the view.

    Keeping both as symbols (rather than "Prev cmd" / "Page up") makes the row
    scannable on a phone; the shape carries the meaning.
    """
    if scrolling:
        return json.dumps({"inline_keyboard": [
            [{"text": "⏫", "callback_data": "s:top"},
             {"text": "🔼", "callback_data": "s:up"},
             {"text": "🔽", "callback_data": "s:down"},
             {"text": "⏬", "callback_data": "s:bottom"}],
            [{"text": "⤓ زنده", "callback_data": "s:live"},
             {"text": "📄 فایل", "callback_data": "k:full"}],
        ]})

    return json.dumps({"inline_keyboard": [
        [{"text": "↵ Enter", "callback_data": "k:enter"},
         {"text": "⛔ Ctrl+C", "callback_data": "k:sigint"},
         {"text": "⇥ Tab", "callback_data": "k:tab"}],
        [{"text": "↑", "callback_data": "k:up"},
         {"text": "↓", "callback_data": "k:down"},
         {"text": "⟳", "callback_data": "k:refresh"}],
        [{"text": "🔼", "callback_data": "s:up"},
         {"text": "🔽", "callback_data": "s:down"},
         {"text": "⌧", "callback_data": "k:clear"}],
        [{"text": "📄 فایل", "callback_data": "k:full"}],
    ]})


def build_message(cfg, body, status):
    header = html.escape(f"{cfg['server_name']} - {cfg['ssh_user']}@{cfg['ssh_host']}")
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    lines = body.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    body = "\n".join(lines)

    if not body.strip():
        return f"\U0001f5a5 <b>{header}</b>\n{html.escape(status)}"

    truncated = False
    if len(body) > MAX_BODY:
        body = body[-MAX_BODY:]
        truncated = True

    while True:
        shown = ("…\n" + body) if truncated else body
        text = (f"\U0001f5a5 <b>{header}</b>\n"
                f"<pre>{html.escape(shown)}</pre>\n{html.escape(status)}")
        if len(text) <= TG_LIMIT:
            return text
        body = body[len(body) // 4:]
        truncated = True
        if len(body) < 200:
            return (f"\U0001f5a5 <b>{header}</b>\n"
                    f"{html.escape(status)}\nخروجی برای یک پیام خیلی بزرگ است.")


def webapp_url(cfg, with_key=False):
    """The Mini App address, or None when it cannot possibly work.

    Telegram's WebView refuses self-signed certificates, so advertising the
    button in that case would only produce a blank screen.
    """
    if not cfg.get("miniapp", True) or cfg["mode"] != "webhook":
        return None
    if not cfg.get("tls") or cfg.get("upload_certificate"):
        return None
    if not cfg.get("webhook_domain"):
        return None
    url = (f"https://{cfg['webhook_domain']}:{cfg['port']}"
           f"/{cfg['webhook_path']}/app")
    if with_key and cfg.get("webapp_key"):
        url += "?k=" + urllib.parse.quote(str(cfg["webapp_key"]))
    return url


LRM = "\u200e"


def ltr(text):
    """Keep a latin token upright inside a right-to-left sentence.

    Without the marks, "/new" in Persian text renders as "new/" because the
    slash is direction-neutral and picks up the paragraph direction.
    """
    return LRM + str(text) + LRM


def code(text):
    """A latin token, pinned left-to-right."""
    return "<code>" + html.escape(ltr(text)) + "</code>"


def val(text):
    """Render a value without forcing a direction it does not want.

    Pinning Persian inside an LTR span reverses it, so only pure-ASCII values
    get the code treatment.
    """
    text = str(text)
    if all(ord(ch) < 128 for ch in text):
        return code(text)
    return "<b>" + html.escape(text) + "</b>"


MENU_COMMANDS = [
    ("start", "باز کردن ترمینال و نمایش راهنما"),
    ("app", "باز کردن مینی‌اپ ترمینال"),
    ("status", "وضعیت سرور و سشن"),
    ("clear", "پاک کردن صفحه"),
    ("full", "فرستادن کل خروجی به‌صورت فایل"),
    ("key", "فرستادن یک کلید خام مثل بالا، پایین یا تب"),
    ("new", "بستن شل و ساختن یک شل تازه"),
    ("close", "بستن سشن"),
    ("help", "راهنما"),
]


class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tg = Telegram(cfg["bot_token"], cfg["api_base"])
        self.sessions = SessionManager(cfg)
        self.uid = cfg["user_id"]
        self.started = time.time()
        self.rejected = deque(maxlen=200)
        self._stop = threading.Event()
        self._menu_done = False
        self.tokens = miniapp.Tokens()
        self.webapp_url = webapp_url(cfg)

    def _stream(self, session, chat_id, message_id=None, seed="", expect_output=True):
        cfg = self.cfg
        gen = session.bump_stream()
        edit_interval = float(cfg["edit_interval"])
        idle_stop = float(cfg["idle_stop"])
        max_stream = float(cfg["max_stream"])
        baseline = session.last_data
        grace = 3.0

        if message_id is None:
            sent = self.tg.send_message(
                chat_id, build_message(cfg, seed or "…", "⏳ در حال اجرا…"),
                self.keyboard())
            if not sent:
                log.warning("could not create output message")
                return
            message_id = sent["message_id"]

        start = time.time()
        last_edit = 0.0
        last_text = None
        settle = 0.5
        idle_since = None

        while True:
            if session.stream_gen != gen:
                return
            now = time.time()
            if not session.alive:
                status = "⚠ شل بسته شد، " + ltr("/new") + " بزنید"
                break
            if now - start >= max_stream:
                status = "⏳ هنوز در حال اجراست، ⟳ را بزنید"
                break

            busy = session.busy()
            quiet = now - session.last_data

            if expect_output and session.last_data <= baseline and now - start < grace:
                session.data_event.clear()
                session.data_event.wait(0.15)
                continue

            if busy is None:
                if quiet >= idle_stop and now - start >= 0.7:
                    status = "✅ انجام شد"
                    break
            elif busy:
                idle_since = None
            else:
                if idle_since is None:
                    idle_since = now
                elif now - idle_since >= settle and quiet >= settle and now - start >= 0.4:
                    status = "✅ انجام شد"
                    break

            if now - last_edit >= edit_interval:
                text = build_message(cfg, session.render(), "⏳ در حال اجرا…")
                if text != last_text:
                    if self.tg.edit_message(chat_id, message_id, text, self.keyboard()):
                        last_text = text
                    last_edit = time.time()
                else:
                    last_edit = now

            session.data_event.clear()
            session.data_event.wait(0.25)

        if session.stream_gen != gen:
            return
        final = build_message(self.cfg, session.render(), status)
        self.tg.edit_message(chat_id, message_id, final, self.keyboard())

    def _spawn_stream(self, *args, **kwargs):
        threading.Thread(target=self._safe, args=(self._stream,) + args,
                         kwargs=kwargs, daemon=True, name="streamer").start()

    @staticmethod
    def _safe(fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception:
            log.exception("worker crashed")

    def keyboard(self, scrolling=False):
        return build_keyboard(scrolling, None if scrolling else self.webapp_url)

    def _show_scroll(self, session, chat_id, message_id):
        """Repaint a message as a scrollback window."""
        session.bump_stream()
        text, offset, total = session.view()
        shown = min(total, session.term.rows)
        first = max(1, total - offset - shown + 1)
        last = max(first, total - offset)
        if offset <= 0:
            status = f"⤓ زنده · {total} خط"
            markup = self.keyboard()
        else:
            status = f"⇡ خط {first} تا {last} از {total}"
            markup = self.keyboard(scrolling=True)
        body = build_message(self.cfg, text, status)
        if message_id:
            self.tg.edit_message(chat_id, message_id, body, markup)
        else:
            self.tg.send_message(chat_id, body, markup)

    def sync_menu(self):
        """Publish the / command menu, visible to the owner only.

        The default and all_private_chats scopes are cleared first, so a
        stranger who finds the bot sees an empty menu rather than a list of
        shell commands. Chat scope needs the chat to exist, which is why this
        is retried after the first authorised message.
        """
        commands = [{"command": name, "description": text}
                    for name, text in MENU_COMMANDS]
        for scope in ({"type": "default"}, {"type": "all_private_chats"}):
            self.tg.call("deleteMyCommands", {"scope": scope}, retries=0)

        result = self.tg.call(
            "setMyCommands",
            {"commands": commands, "scope": {"type": "chat", "chat_id": self.uid}},
            retries=0, quiet_errors=("chat not found",))
        if not result:
            log.info("command menu not published yet; will retry after /start")
            return False

        if self.webapp_url:
            button = {"type": "web_app", "text": "Terminal",
                      "web_app": {"url": self.webapp_url}}
        else:
            button = {"type": "commands"}
        self.tg.call("setChatMenuButton",
                     {"chat_id": self.uid, "menu_button": button},
                     retries=0, quiet_errors=("chat not found",))
        self._menu_done = True
        log.info("published %d commands to the menu", len(commands))
        return True

    def _ensure_menu(self):
        if self._menu_done:
            return
        self._menu_done = True
        threading.Thread(target=self._safe, args=(self.sync_menu,),
                         daemon=True, name="menu").start()

    def _reply(self, chat_id, body, status="", keyboard=None):
        self.tg.send_message(chat_id, build_message(self.cfg, body, status),
                             keyboard or self.keyboard())

    def _notice(self, chat_id, html_body, keyboard=None):
        """Prose for the user, outside the terminal frame.

        Persian inside <pre> fights the monospace grid and reads badly, so
        guides and status reports go out as ordinary formatted text and let
        Telegram lay them out right-to-left.
        """
        self.tg.send_message(chat_id, html_body, keyboard)

    def _cmd_welcome(self, chat_id):
        server = html.escape(self.cfg["server_name"])
        parts = [
            f"<b>🖥 ترمینال سرور {server}</b>",
            "",
            "هر متنی بفرستید، همان‌جا روی سرور اجرا می‌شود.",
            "شل باز می‌ماند، پس " + code("cd") + " و متغیرهایی که "
            "تعریف می‌کنید به پیام بعدی هم می‌رسند.",
            "",
            "<b>دکمه‌های زیر هر پیام</b>",
            "↵ اینتر · ⛔ کنترل+C · ⇥ تب",
            "↑ ↓ تاریخچهٔ دستورهای شل",
            "🔼 🔽 بالا و پایین رفتن در خروجی‌های قبلی",
            "⟳ تازه‌سازی · ⌧ پاک کردن صفحه",
            "📄 گرفتن کل خروجی به‌صورت فایل",
            "",
            "<b>چند نکته</b>",
            "دستورهای طولانی قطع نمی‌شوند. اگر پیام گفت هنوز در حال "
            "اجراست، ⟳ را بزنید تا ادامه‌اش را ببینید.",
            "هر پیام حدود ۵۵ خط جا می‌دهد. خروجی قدیمی‌تر زیر دکمهٔ 🔼 "
            "است یا با " + code("/full") + " به‌صورت فایل می‌آید.",
        ]
        if self.webapp_url:
            parts += [
                "دکمهٔ <b>Terminal</b> کنار کادر پیام، اپ کامل را باز "
                "می‌کند: اسکرول واقعی و مرورگر فایل، روی همین سشن.",
            ]
        else:
            parts += [
                "برای ترمینال کامل و مرورگر فایل، " + code("/app") +
                " را بزنید تا بگوید چه چیزی لازم دارد.",
            ]
        parts += [
            "",
            "برای دیدن فهرست دستورها، در چت " + code("/") + " را تایپ کنید.",
        ]
        self._notice(chat_id, "\n".join(parts), self.keyboard())

    def _cmd_help(self, chat_id):
        rows = [
            ("/start", "باز کردن ترمینال و نمایش راهنما"),
            ("/app", "باز کردن مینی‌اپ ترمینال"),
            ("/status", "وضعیت سرور و سشن"),
            ("/full", "فرستادن کل خروجی به‌صورت فایل"),
            ("/clear", "پاک کردن صفحه"),
            ("/new", "بستن شل و ساختن یک شل تازه"),
            ("/close", "بستن سشن"),
            ("/help", "همین راهنما"),
        ]
        parts = ["<b>📖 راهنمای دستورها</b>", ""]
        parts += [f"{code(name)} — {desc}" for name, desc in rows]
        parts += [
            "",
            f"{code('/key <نام>')} — فرستادن یک کلید خام:",
            ltr(" ".join(code(k) for k in
                         ("up", "down", "left", "right", "esc", "tab"))),
            ltr(" ".join(code(k) for k in
                         ("enter", "sigint", "sigtstp", "eof", "clear"))),
            "",
            "هر متن دیگری که بفرستید، روی سرور اجرا می‌شود.",
        ]
        self._notice(chat_id, "\n".join(parts), self.keyboard())

    def _cmd_status(self, chat_id):
        session = self.sessions.get(self.uid, create=False)
        rows = []

        def dur(seconds):
            seconds = int(seconds)
            days, rest = divmod(seconds, 86400)
            hours, rest = divmod(rest, 3600)
            minutes = rest // 60
            out = []
            if days:
                out.append(f"{days} روز")
            if hours:
                out.append(f"{hours} ساعت")
            out.append(f"{minutes} دقیقه")
            return " و ".join(out)

        try:
            with open("/proc/uptime", "r") as fh:
                rows.append(("روشن‌بودن سرور", dur(float(fh.read().split()[0]))))
        except (OSError, ValueError, IndexError):
            pass
        rows.append(("روشن‌بودن ربات", dur(time.time() - self.started)))
        rows.append(("حالت اتصال", "وب‌هوک" if self.cfg["mode"] == "webhook"
                     else "پولینگ"))
        rows.append(("اجرا با کاربر", f"uid {os.getuid()}"))
        try:
            load = os.getloadavg()
            rows.append(("بار پردازنده", f"{load[0]:.2f} · {load[1]:.2f} · {load[2]:.2f}"))
        except OSError:
            pass
        try:
            with open("/proc/meminfo", "r") as fh:
                info = {}
                for line in fh:
                    key, _, rest = line.partition(":")
                    info[key] = int(rest.split()[0])
            total = info.get("MemTotal", 0) // 1024
            avail = info.get("MemAvailable", 0) // 1024
            rows.append(("حافظه", f"{total - avail} از {total} مگابایت"))
        except (OSError, ValueError, IndexError):
            pass
        if session and session.alive:
            rows.append(("شل", f"pid {session.proc.pid} · "
                               f"{dur(time.time() - session.created)}"))
        else:
            rows.append(("شل", "در حال اجرا نیست"))
        if self.rejected:
            rows.append(("درخواست‌های ردشده", str(len(self.rejected))))

        parts = [f"<b>📊 وضعیت — SST {VERSION}</b>", ""]
        parts += [f"{html.escape(label)}: {val(value)}" for label, value in rows]
        self._notice(chat_id, "\n".join(parts), self.keyboard())

    def _cmd_app(self, chat_id):
        if self.webapp_url:
            markup = json.dumps({"inline_keyboard": [[
                {"text": "🖥 باز کردن ترمینال",
                 "web_app": {"url": self.webapp_url}}]]})
            self._notice(chat_id,
                         "<b>🖥 مینی‌اپ ترمینال</b>\n\n"
                         "ترمینال کامل به‌همراه مرورگر فایل، روی همان سشنی "
                         "که در چت دارید.\n"
                         "همین دکمه کنار کادر پیام هم همیشه در دسترس است.",
                         markup)
            direct = webapp_url(self.cfg, with_key=True)
            if direct and direct != self.webapp_url:
                self._notice(chat_id,
                             "لینک باز کردن در مرورگر معمولی. این لینک "
                             "دسترسی کامل می‌دهد، پس جایی نفرستیدش:\n"
                             f"{code(direct)}")
            return

        if self.cfg["mode"] != "webhook":
            reason = ("مینی‌اپ فقط در حالت وب‌هوک کار می‌کند و سرویس شما "
                      "روی پولینگ است. برای فعال شدنش به یک دامنه و گواهی "
                      "معتبر نیاز دارید.")
        elif self.cfg.get("upload_certificate"):
            reason = ("گواهی شما self-signed است و مرورگر داخلی تلگرام "
                      "قبولش نمی‌کند. با یک گواهی معتبر (مثلاً Let's Encrypt) "
                      "مینی‌اپ بالا می‌آید.")
        elif not self.cfg.get("miniapp", True):
            reason = ("مینی‌اپ در تنظیمات خاموش است. در " + code("config.json") +
                      " مقدار " + code('"miniapp": true') + " را بگذارید و "
                      "سرویس را ری‌استارت کنید.")
        else:
            reason = "مینی‌اپ در این پیکربندی در دسترس نیست."
        self._notice(chat_id, "<b>🖥 مینی‌اپ</b>\n\n" + reason, self.keyboard())

    def _cmd_full(self, chat_id):
        session = self.sessions.get(self.uid, create=False)
        if not session:
            self._reply(chat_id, "", "⚠ سشنی باز نیست، " + ltr("/start") + " بزنید")
            return
        data = session.transcript()
        if not data.strip():
            self._reply(chat_id, "", "⚠ هنوز خروجی‌ای ثبت نشده")
            return
        name = time.strftime("sst-%Y%m%d-%H%M%S.txt")
        ok = self.tg.send_document(chat_id, name, data.encode("utf-8"),
                                   caption=f"{len(data.splitlines())} خط")
        if not ok:
            self._reply(chat_id, data[-MAX_BODY:], "⚠ ارسال فایل نشد")

    def handle_update(self, update):
        try:
            if "callback_query" in update:
                self._handle_callback(update["callback_query"])
            elif "message" in update:
                self._handle_message(update["message"])
        except Exception:
            log.exception("failed to handle update")

    def _authorised(self, sender, chat):
        """Owner only, and only in the owner's own private chat."""
        if not sender or not chat:
            return False
        if sender.get("id") != self.uid:
            log.warning("ignoring update from user %s", sender.get("id"))
            return False
        if chat.get("type") != "private" or chat.get("id") != self.uid:
            log.warning("ignoring update in chat %s (%s)", chat.get("id"),
                        chat.get("type"))
            return False
        return True

    def _handle_message(self, msg):
        if not self._authorised(msg.get("from"), msg.get("chat")):
            return
        chat_id = msg["chat"]["id"]
        self._ensure_menu()
        text = (msg.get("text") or "").strip()
        if not text:
            return

        cmd = text.split(maxsplit=1)[0].lower().split("@")[0]
        arg = text.split(maxsplit=1)[1] if " " in text else ""

        if cmd == "/start":
            self.sessions.get(self.uid)
            self._cmd_welcome(chat_id)
            return
        if cmd == "/help":
            self._cmd_help(chat_id)
            return
        if cmd == "/status":
            self._cmd_status(chat_id)
            return
        if cmd == "/app":
            self._cmd_app(chat_id)
            return
        if cmd == "/full":
            self._cmd_full(chat_id)
            return
        if cmd == "/new":
            self.sessions.kill(self.uid)
            self.sessions.get(self.uid)
            self._reply(chat_id, "", "✅ شل تازه آماده است")
            return
        if cmd == "/close":
            self.sessions.kill(self.uid)
            self._reply(chat_id, "", "⛔ سشن بسته شد")
            return
        if cmd == "/clear":
            session = self.sessions.get(self.uid)
            session.clear()
            self._reply(chat_id, "", "✅ صفحه پاک شد")
            return
        if cmd == "/key":
            key = arg.strip().lower()
            if key not in KEY_MAP:
                self._notice(chat_id,
                             "کلید " + code(key or "خالی") + " را نمی‌شناسم.\n"
                             "کلیدهای معتبر:\n" +
                             ltr(" ".join(code(k) for k in sorted(KEY_MAP))),
                             self.keyboard())
                return
            session = self.sessions.get(self.uid)
            self._send_key(session, chat_id, key, None)
            return

        session = self.sessions.get(self.uid)
        session.scroll_live()
        try:
            session.write(text + "\r")
        except BrokenPipeError as exc:
            log.warning("write failed: %s", exc)
            self.sessions.kill(self.uid)
            self._reply(chat_id, "", "⚠ شل از بین رفت، " + ltr("/new") + " بزنید")
            return
        self._spawn_stream(session, chat_id)

    def _handle_callback(self, cq):
        cq_id = cq.get("id")
        sender = cq.get("from")
        message = cq.get("message") or {}
        chat = message.get("chat")

        if not self._authorised(sender, chat):
            if cq_id:
                self.tg.answer_callback(cq_id, "دسترسی ندارید")
            return

        chat_id = chat["id"]
        message_id = message.get("message_id")
        data = str(cq.get("data") or "")
        self.tg.answer_callback(cq_id)

        if not (data.startswith("k:") or data.startswith("s:")):
            return
        action = data[2:]

        if data.startswith("s:"):
            session = self.sessions.get(self.uid)
            if action == "up":
                session.page_by(+1)
            elif action == "down":
                session.page_by(-1)
            elif action == "top":
                session.scroll_to_top()
            elif action in ("live", "bottom"):
                session.scroll_live()
            self._show_scroll(session, chat_id, message_id)
            return

        if action == "noop":
            return
        if action == "full":
            self._cmd_full(chat_id)
            return

        session = self.sessions.get(self.uid)
        if action == "refresh":
            self._spawn_stream(session, chat_id, message_id, expect_output=False)
            return
        if action == "clear":
            session.clear()
            self.tg.edit_message(chat_id, message_id,
                                 build_message(self.cfg, "", "✅ صفحه پاک شد"),
                                 self.keyboard())
            return
        if action in KEY_MAP:
            self._send_key(session, chat_id, action, message_id)

    def _send_key(self, session, chat_id, key, message_id):
        try:
            session.write(KEY_MAP[key])
        except BrokenPipeError as exc:
            log.warning("key write failed: %s", exc)
            self.sessions.kill(self.uid)
            self._reply(chat_id, "", "⚠ شل از بین رفت، " + ltr("/new") + " بزنید")
            return
        self._spawn_stream(session, chat_id, message_id)

    def housekeeping(self):
        while not self._stop.wait(60):
            try:
                self.sessions.reap_idle()
            except Exception:
                log.exception("reaper failed")

    def stop(self):
        self._stop.set()
        self.sessions.shutdown()


def make_handler(bot, cfg):
    secret = str(cfg["webhook_secret"])
    expected_path = "/" + str(cfg["webhook_path"])
    ip_filter = bool(cfg["ip_filter"])
    max_body = 2 * 1024 * 1024
    app_enabled = bool(cfg.get("miniapp", True))
    app_key = str(cfg.get("webapp_key") or "")
    tokens = bot.tokens

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "nginx"
        sys_version = ""

        def _deny(self, code, reason):
            peer = self.client_address[0] if self.client_address else "?"
            bot.rejected.append((time.time(), peer, reason))
            log.warning("rejected %s from %s (%s %s)", reason, peer,
                        self.command, self.path[:60])
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _ok(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = b'{"ok":true}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _route(self):
            """Split the request into (webhook | app subpath | unknown)."""
            path = urllib.parse.urlsplit(self.path).path
            if hmac.compare_digest(path, expected_path):
                return "hook", ""
            prefix = expected_path + "/"
            if app_enabled and path.startswith(prefix):
                return "app", path[len(prefix):]
            return "none", ""

        def _token_ok(self, query):
            supplied = self.headers.get("X-SST-Token") or query.get("t", [""])[0]
            return tokens.valid(supplied)

        def do_GET(self):
            kind, sub = self._route()
            if kind != "app":
                return self._deny(404, "GET")
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

            if sub in ("app", "app/"):
                page = webapp.PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Options", "ALLOWALL")
                self.end_headers()
                self.wfile.write(page)
                return

            if sub == "ws":
                return self._websocket(query)

            if not self._token_ok(query):
                return self._deny(403, "app token")

            path = query.get("path", ["/"])[0]
            if sub == "api/ls":
                return self._json(miniapp.list_dir(path))
            if sub == "api/cat":
                return self._json(miniapp.read_file(path))
            if sub == "api/dl":
                return self._download(path)
            return self._deny(404, "app route")

        def _download(self, path):
            found = miniapp.file_for_download(path)
            if not found:
                return self._json({"ok": False, "error": "not a file"}, 404)
            full, size, ctype = found
            name = os.path.basename(full) or "download"
            try:
                handle = open(full, "rb")
            except OSError as exc:
                return self._json({"ok": False, "error": str(exc)}, 403)
            with handle:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{name}"')
                self.end_headers()
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except OSError:
                        break

        def _websocket(self, query):
            if not self._token_ok(query):
                return self._deny(403, "ws token")
            key = self.headers.get("Sec-WebSocket-Key", "")
            upgrade = (self.headers.get("Upgrade") or "").lower()
            if upgrade != "websocket" or not key:
                return self._deny(400, "bad ws upgrade")

            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept",
                             miniapp.WebSocket.accept_key(key))
            self.end_headers()
            self.close_connection = True

            try:
                self.connection.settimeout(None)
            except OSError:
                pass

            session = bot.sessions.get(bot.uid)
            sock = miniapp.WebSocket(self.rfile, self.wfile)
            log.info("miniapp terminal attached from %s", self.client_address[0])
            try:
                miniapp.relay(sock, session, bot)
            except Exception:
                log.exception("miniapp relay failed")
            finally:
                try:
                    session.restore_size()
                except Exception:
                    pass
                log.info("miniapp terminal detached")

        do_HEAD = do_GET
        do_PUT = do_GET
        do_DELETE = do_GET

        def do_POST(self):
            kind, sub = self._route()
            if kind == "app":
                if sub != "api/auth":
                    return self._deny(404, "app route")
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(min(length, 8192)) or b"{}")
                except (ValueError, OSError):
                    return self._json({"ok": False, "error": "bad request"}, 400)

                user = miniapp.verify_init_data(payload.get("init_data", ""),
                                                cfg["bot_token"], bot.uid)
                if user is None:
                    supplied = str(payload.get("key") or "")
                    if not (app_key and hmac.compare_digest(supplied, app_key)):
                        peer = self.client_address[0]
                        bot.rejected.append((time.time(), peer, "miniapp auth"))
                        log.warning("miniapp auth rejected from %s", peer)
                        return self._json({"ok": False, "error": "not authorised"}, 403)
                    user = {"username": cfg["ssh_user"]}
                return self._json({
                    "ok": True,
                    "token": tokens.issue(),
                    "server": cfg["server_name"],
                    "user": user.get("username") or cfg["ssh_user"],
                    "host": cfg["ssh_host"],
                })

            if ip_filter:
                try:
                    peer = ipaddress.ip_address(self.client_address[0])
                    if peer.version == 6 and peer.ipv4_mapped:
                        peer = peer.ipv4_mapped
                    if not any(peer in net for net in TELEGRAM_NETS):
                        return self._deny(403, "non-telegram source IP")
                except ValueError:
                    return self._deny(403, "unparsable source IP")

            if kind != "hook":
                return self._deny(404, "bad path")

            got = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not hmac.compare_digest(got, secret):
                return self._deny(403, "bad secret token")

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._deny(400, "bad content-length")
            if length <= 0 or length > max_body:
                return self._deny(400, "bad body size")

            try:
                raw = self.rfile.read(length)
                update = json.loads(raw.decode("utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                return self._deny(400, "bad JSON")

            self._ok()
            threading.Thread(target=bot.handle_update, args=(update,),
                             daemon=True, name="update").start()

        def log_message(self, *args):
            pass

    return Handler


def build_ssl_context(cfg):
    import ssl
    cert, key = cfg["cert"], cfg["key"]
    if not (os.path.exists(cert) and os.path.exists(key)):
        raise ConfigError(
            f"TLS certificate not found ({cert}).\n"
            "Run setup.sh, or set \"tls\": false if you terminate TLS upstream."
        )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx


def register_webhook(bot, cfg):
    """Register the webhook, uploading the cert when it is self-signed."""
    url = f"https://{cfg['webhook_domain']}:{cfg['port']}/{cfg['webhook_path']}"
    params = {
        "url": url,
        "secret_token": cfg["webhook_secret"],
        "allowed_updates": ["message", "callback_query"],
        "max_connections": 10,
        "drop_pending_updates": True,
    }
    if cfg["upload_certificate"]:
        try:
            with open(cfg["cert"], "rb") as fh:
                cert = fh.read()
        except OSError as exc:
            log.error("cannot read certificate for upload: %s", exc)
            return False
        result = bot.tg.upload("setWebhook", params, {"certificate": ("cert.pem", cert)})
    else:
        result = bot.tg.call("setWebhook", params)

    if not result:
        log.error("setWebhook failed, the bot will not receive anything")
        return False
    log.info("webhook registered: https://%s:%s/<secret-path>",
             cfg["webhook_domain"], cfg["port"])

    info = bot.tg.call("getWebhookInfo", {})
    if isinstance(info, dict) and info.get("last_error_message"):
        log.warning("telegram reports a webhook error: %s", info["last_error_message"])
    return True


def run_webhook(bot, cfg):
    handler = make_handler(bot, cfg)
    server = ThreadingHTTPServer((cfg["bind"], int(cfg["port"])), handler)
    server.daemon_threads = True
    if cfg["tls"]:
        server.socket = build_ssl_context(cfg).wrap_socket(server.socket, server_side=True)

    threading.Thread(target=register_webhook, args=(bot, cfg), daemon=True).start()

    log.info("listening on %s:%s (%s)", cfg["bind"], cfg["port"],
             "https" if cfg["tls"] else "http")
    return server


def run_polling(bot, cfg):
    """Long-poll getUpdates."""
    bot.tg.call("deleteWebhook", {"drop_pending_updates": True})
    log.info("polling for updates")
    offset = 0
    backoff = 1.0
    while not bot._stop.is_set():
        result = bot.tg.call("getUpdates",
                             {"offset": offset, "timeout": 30,
                              "allowed_updates": ["message", "callback_query"]},
                             timeout=45, retries=0)
        if result is None:
            time.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)
            continue
        backoff = 1.0
        for update in result:
            offset = max(offset, update.get("update_id", 0) + 1)
            threading.Thread(target=bot.handle_update, args=(update,),
                             daemon=True, name="update").start()


def main():
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"[config error] {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, str(cfg["log_level"]).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    check_permissions(CONFIG_PATH)

    if os.getuid() == 0:
        log.warning("running as root: this bot hands root to whoever controls "
                    "the Telegram account %s", cfg["user_id"])

    bot = Bot(cfg)
    threading.Thread(target=bot.housekeeping, daemon=True, name="reaper").start()

    server = None

    def shutdown(signum, _frame):
        log.info("signal %s received, shutting down", signum)
        bot.stop()
        if server is not None:
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("SST v%s starting (mode=%s)", VERSION, cfg["mode"])
    threading.Thread(target=bot._safe, args=(bot.sync_menu,), daemon=True,
                     name="menu").start()
    try:
        if cfg["mode"] == "webhook":
            server = run_webhook(bot, cfg)
            server.serve_forever()
        else:
            run_polling(bot, cfg)
    except KeyboardInterrupt:
        pass
    except ConfigError as exc:
        print(f"[config error] {exc}", file=sys.stderr)
        return 2
    finally:
        bot.stop()
        if server is not None:
            try:
                server.server_close()
            except Exception:
                pass
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
