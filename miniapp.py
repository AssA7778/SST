"""Mini App back end: initData validation, WebSocket relay and the file API."""

import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import struct
import threading
import time
import urllib.parse

log = logging.getLogger("sst")

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_PREVIEW = 256 * 1024
MAX_LISTING = 3000
TOKEN_TTL = 8 * 3600
INITDATA_MAX_AGE = 24 * 3600


class Tokens:
    """Short-lived bearer tokens handed out after a verified initData check."""

    def __init__(self, ttl=TOKEN_TTL):
        self._ttl = ttl
        self._store = {}
        self._lock = threading.Lock()

    def issue(self):
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._store[token] = time.time() + self._ttl
            if len(self._store) > 64:
                now = time.time()
                for key in [k for k, exp in self._store.items() if exp < now]:
                    self._store.pop(key, None)
        return token

    def valid(self, token):
        if not token:
            return False
        with self._lock:
            exp = self._store.get(token)
            if exp is None:
                return False
            if exp < time.time():
                self._store.pop(token, None)
                return False
        return True

    def clear(self):
        with self._lock:
            self._store.clear()


def verify_init_data(init_data, bot_token, expected_uid, max_age=INITDATA_MAX_AGE):
    """Validate Telegram Mini App initData.

    Telegram signs the payload with HMAC-SHA256 under a key derived from the
    bot token, so a forged initData cannot be produced without it. The user id
    and the age of the signature are checked too: a valid signature for the
    wrong account, or a replayed one from last month, is still a reject.
    """
    if not init_data or not isinstance(init_data, str):
        return None
    try:
        pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True,
                                       strict_parsing=True)
    except ValueError:
        return None

    data = dict(pairs)
    supplied = data.pop("hash", "")
    if not supplied:
        return None

    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        return None

    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError:
        return None
    if max_age and (time.time() - auth_date) > max_age:
        return None

    try:
        user = json.loads(data.get("user", "{}"))
    except json.JSONDecodeError:
        return None
    if int(user.get("id", 0)) != int(expected_uid):
        return None
    return user


def safe_path(raw):
    """Normalise a client-supplied path to an absolute one with no traversal."""
    if not raw:
        return "/"
    cleaned = str(raw).replace("\0", "").lstrip("/")
    path = os.path.normpath("/" + cleaned)
    return path if path.startswith("/") and not path.startswith("//") else "/"


def list_dir(path):
    path = safe_path(path)
    if not os.path.isdir(path):
        return {"ok": False, "error": "not a directory"}
    try:
        names = sorted(os.listdir(path), key=lambda n: n.lower())
    except PermissionError:
        return {"ok": False, "error": "permission denied"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    entries = []
    for name in names[:MAX_LISTING]:
        full = os.path.join(path, name)
        item = {"name": name}
        try:
            item["link"] = os.path.islink(full)
            stat = os.stat(full)
            item["dir"] = os.path.isdir(full)
            if not item["dir"]:
                item["size"] = stat.st_size
                item["exec"] = bool(stat.st_mode & 0o111)
            item["mtime"] = int(stat.st_mtime)
        except OSError:
            item["dir"] = False
            item["size"] = None
        entries.append(item)
    entries.sort(key=lambda e: (not e.get("dir"), e["name"].lower()))
    truncated = len(names) > MAX_LISTING
    return {"ok": True, "path": path, "entries": entries, "truncated": truncated}


def read_file(path):
    path = safe_path(path)
    if not os.path.isfile(path):
        return {"ok": False, "error": "not a file"}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            raw = fh.read(MAX_PREVIEW)
    except PermissionError:
        return {"ok": False, "error": "permission denied"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    if b"\0" in raw[:8192]:
        return {"ok": False, "error": f"binary file ({size} bytes) - use download"}
    text = raw.decode("utf-8", errors="replace")
    if size > MAX_PREVIEW:
        text += f"\n\n[truncated at {MAX_PREVIEW} of {size} bytes]"
    return {"ok": True, "path": path, "size": size, "data": text}


def file_for_download(path):
    path = safe_path(path)
    if not os.path.isfile(path):
        return None
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return path, os.path.getsize(path), ctype


class WebSocket:
    """Minimal RFC 6455 server endpoint over an already-accepted socket."""

    TEXT, BINARY, CLOSE, PING, PONG = 0x1, 0x2, 0x8, 0x9, 0xA

    def __init__(self, rfile, wfile):
        self.rfile = rfile
        self.wfile = wfile
        self._send_lock = threading.Lock()
        self.closed = False

    @staticmethod
    def accept_key(client_key):
        digest = hashlib.sha1((client_key + WS_GUID).encode()).digest()
        return base64.b64encode(digest).decode()

    def send(self, payload, opcode=TEXT):
        if self.closed:
            return False
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        header = bytearray([0x80 | opcode])
        length = len(data)
        if length < 126:
            header.append(length)
        elif length < (1 << 16):
            header.append(126)
            header += struct.pack("!H", length)
        else:
            header.append(127)
            header += struct.pack("!Q", length)
        try:
            with self._send_lock:
                self.wfile.write(bytes(header) + data)
                self.wfile.flush()
            return True
        except (OSError, ValueError):
            self.closed = True
            return False

    def _read_exact(self, n):
        chunks = []
        while n > 0:
            chunk = self.rfile.read(n)
            if not chunk:
                raise ConnectionError("peer closed")
            chunks.append(chunk)
            n -= len(chunk)
        return b"".join(chunks)

    def receive(self):
        """Next application message, or None when the peer goes away."""
        buffer = bytearray()
        opcode = None
        while True:
            try:
                first, second = self._read_exact(2)
            except (ConnectionError, OSError):
                self.closed = True
                return None

            fin = bool(first & 0x80)
            frame_op = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            try:
                if length == 126:
                    length = struct.unpack("!H", self._read_exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", self._read_exact(8))[0]
                if length > 4 * 1024 * 1024:
                    self.close()
                    return None
                mask = self._read_exact(4) if masked else None
                payload = self._read_exact(length) if length else b""
            except (ConnectionError, OSError, struct.error):
                self.closed = True
                return None

            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

            if frame_op == self.CLOSE:
                self.close()
                return None
            if frame_op == self.PING:
                self.send(payload, self.PONG)
                continue
            if frame_op == self.PONG:
                continue
            if frame_op in (self.TEXT, self.BINARY):
                opcode = frame_op
                buffer = bytearray(payload)
            elif frame_op == 0x0:
                buffer += payload
            else:
                continue

            if fin:
                if opcode == self.TEXT:
                    try:
                        return buffer.decode("utf-8")
                    except UnicodeDecodeError:
                        return ""
                return bytes(buffer)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            with self._send_lock:
                self.wfile.write(b"\x88\x00")
                self.wfile.flush()
        except (OSError, ValueError):
            pass


def relay(ws, session, bot, min_interval=0.06, max_idle=900):
    """Push screen updates to the client and feed its keystrokes to the PTY."""
    stop = threading.Event()

    def pump():
        last = None
        last_send = 0.0
        while not stop.is_set() and not ws.closed:
            session.data_event.wait(0.15)
            session.data_event.clear()
            if stop.is_set():
                break
            now = time.time()
            if now - last_send < min_interval:
                time.sleep(min_interval - (now - last_send))
            if not session.alive:
                ws.send(json.dumps({"t": "screen", "d": "shell exited",
                                    "busy": False}))
                break
            text = session.render()
            busy = bool(session.busy())
            frame = json.dumps({"t": "screen", "d": text, "busy": busy})
            if frame != last:
                if not ws.send(frame):
                    break
                last = frame
                last_send = time.time()

    pumper = threading.Thread(target=pump, daemon=True, name="ws-pump")
    pumper.start()
    deadline = time.time() + max_idle
    try:
        while not ws.closed:
            if time.time() > deadline:
                break
            message = ws.receive()
            if message is None:
                break
            deadline = time.time() + max_idle
            if not isinstance(message, str):
                continue
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                continue

            kind = event.get("t")
            if kind == "in":
                data = event.get("d")
                if isinstance(data, str) and data:
                    try:
                        session.scroll_live()
                        session.write(data[:4096])
                    except (BrokenPipeError, OSError) as exc:
                        log.warning("miniapp write failed: %s", exc)
                        break
                    session.data_event.set()
            elif kind in ("resize", "hello"):
                try:
                    cols = int(event.get("cols") or 0)
                    rows = int(event.get("rows") or 0)
                except (TypeError, ValueError):
                    cols = rows = 0
                if cols and rows:
                    session.resize(cols, rows)
                session.data_event.set()
    finally:
        stop.set()
        session.data_event.set()
        ws.close()
