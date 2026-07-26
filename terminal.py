"""Minimal VT100/xterm screen emulator."""

from collections import deque

__all__ = ["Terminal"]

_GROUND = 0
_ESC = 1
_CSI = 2
_OSC = 3
_ESC_INT = 4

_CSI_FINAL = set(range(0x40, 0x7F))
_CSI_PARAM = set(b"0123456789;:<=>?")
_CSI_INTER = set(b" !\"#$%&'()*+,-./")


class Terminal:
    """A fixed-size character grid fed with raw PTY output."""

    def __init__(self, cols=60, rows=32, scrollback=4000):
        self.cols = max(20, int(cols))
        self.rows = max(4, int(rows))
        self.scrollback = deque(maxlen=scrollback)
        self._alt = None
        self._reset_buffer()
        self._state = _GROUND
        self._buf = ""

    def _reset_buffer(self):
        self.grid = [[" "] * self.cols for _ in range(self.rows)]
        self.cx = 0
        self.cy = 0
        self.top = 0
        self.bot = self.rows - 1
        self.saved = (0, 0)
        self.wrap_pending = False

    def reset(self):
        """Full terminal reset (RIS)."""
        self._alt = None
        self.scrollback.clear()
        self._reset_buffer()
        self._state = _GROUND
        self._buf = ""

    def resize(self, cols, rows):
        cols = max(20, int(cols))
        rows = max(4, int(rows))
        if cols == self.cols and rows == self.rows:
            return
        old = self.grid
        self.cols, self.rows = cols, rows
        self.grid = [[" "] * cols for _ in range(rows)]
        for y in range(min(rows, len(old))):
            for x in range(min(cols, len(old[y]))):
                self.grid[y][x] = old[y][x]
        self.cx = min(self.cx, cols - 1)
        self.cy = min(self.cy, rows - 1)
        self.top = 0
        self.bot = rows - 1

    @property
    def display(self):
        """Viewport as a list of right-stripped lines."""
        return ["".join(row).rstrip() for row in self.grid]

    def text(self):
        """Viewport as text, with trailing blank lines removed."""
        lines = self.display
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def full_text(self):
        """Scrollback plus viewport."""
        lines = list(self.scrollback) + self.display
        while lines and not lines[-1]:
            lines.pop()
        while lines and not lines[0]:
            lines.pop(0)
        return "\n".join(lines)

    def history(self):
        """Scrollback plus viewport, trailing blank lines trimmed."""
        lines = list(self.scrollback) + self.display
        while lines and not lines[-1]:
            lines.pop()
        return lines

    def page(self, offset=0, rows=None):
        """A window into the history.

        offset counts lines back from the live bottom. Returns the text plus
        (offset, total) clamped to what actually exists, so a caller can page
        without tracking the bounds itself.
        """
        rows = rows or self.rows
        lines = self.history()
        total = len(lines)
        offset = max(0, min(int(offset), max(0, total - rows)))
        end = total - offset
        start = max(0, end - rows)
        return "\n".join(lines[start:end]), offset, total

    def feed(self, text):
        """Feed decoded PTY output. Partial sequences are fine."""
        for ch in text:
            state = self._state
            if state == _GROUND:
                self._ground(ch)
            elif state == _ESC:
                self._escape(ch)
            elif state == _CSI:
                self._csi(ch)
            elif state == _OSC:
                self._osc(ch)
            elif state == _ESC_INT:
                self._state = _GROUND

    def _ground(self, ch):
        o = ord(ch)
        if o == 0x1B:
            self._state = _ESC
            self._buf = ""
        elif ch == "\n" or o == 0x0B or o == 0x0C:
            self.wrap_pending = False
            self._index()
        elif ch == "\r":
            self.wrap_pending = False
            self.cx = 0
        elif ch == "\b":
            self.wrap_pending = False
            if self.cx > 0:
                self.cx -= 1
        elif ch == "\t":
            self.wrap_pending = False
            self.cx = min(self.cols - 1, (self.cx // 8 + 1) * 8)
        elif o == 0x07:
            pass
        elif o < 0x20 or o == 0x7F:
            pass
        else:
            self._put(ch)

    def _put(self, ch):
        if self.wrap_pending:
            self.cx = 0
            self._index()
            self.wrap_pending = False
        self.grid[self.cy][self.cx] = ch
        if self.cx + 1 >= self.cols:
            self.wrap_pending = True
        else:
            self.cx += 1

    def _escape(self, ch):
        if ch == "[":
            self._state = _CSI
            self._buf = ""
        elif ch == "]":
            self._state = _OSC
            self._buf = ""
        elif ch in "()*+-./%":
            self._state = _ESC_INT
        elif ch == "7":
            self.saved = (self.cx, self.cy)
            self._state = _GROUND
        elif ch == "8":
            self.cx, self.cy = self.saved
            self._clamp()
            self._state = _GROUND
        elif ch == "D":
            self._index()
            self._state = _GROUND
        elif ch == "M":
            self._reverse_index()
            self._state = _GROUND
        elif ch == "E":
            self.cx = 0
            self._index()
            self._state = _GROUND
        elif ch == "c":
            self.reset()
        elif ch in "PX^_":
            self._state = _OSC
            self._buf = ""
        else:
            self._state = _GROUND

    def _osc(self, ch):
        o = ord(ch)
        if o == 0x07:
            self._state = _GROUND
            self._buf = ""
        elif o == 0x1B:
            self._buf = "\x1b"
        elif self._buf == "\x1b":
            self._state = _GROUND
            self._buf = ""
        else:
            if len(self._buf) < 4096:
                self._buf += ch

    def _csi(self, ch):
        o = ord(ch)
        if o in _CSI_PARAM or o in _CSI_INTER:
            if len(self._buf) < 128:
                self._buf += ch
            return
        if o in _CSI_FINAL:
            params = self._buf
            self._buf = ""
            self._state = _GROUND
            try:
                self._dispatch_csi(params, ch)
            except Exception:
                pass
            return
        self._state = _GROUND
        self._buf = ""

    def _nums(self, params, default=0, count=1):
        private = params[:1] in ("?", ">", "<", "=")
        body = params[1:] if private else params
        body = body.replace(":", ";")
        parts = body.split(";") if body else []
        out = []
        for p in parts:
            p = p.strip()
            out.append(int(p) if p.isdigit() else default)
        while len(out) < count:
            out.append(default)
        return out

    def _dispatch_csi(self, params, final):
        private = params[:1] in ("?", ">", "<", "=")
        n = self._nums(params, 0, 4)
        a = n[0] if n[0] else 1

        if final == "m":
            return
        if final in "hl" and private:
            for code in self._nums(params, 0, 1):
                if code in (1047, 1049):
                    if final == "h":
                        self._enter_alt()
                    else:
                        self._leave_alt()
            return
        if final in "hl":
            return

        if final == "A":
            self.cy = max(self.top, self.cy - a)
        elif final in ("B", "e"):
            self.cy = min(self.bot, self.cy + a)
        elif final in ("C", "a"):
            self.cx = min(self.cols - 1, self.cx + a)
        elif final == "D":
            self.cx = max(0, self.cx - a)
        elif final == "E":
            self.cy = min(self.bot, self.cy + a)
            self.cx = 0
        elif final == "F":
            self.cy = max(self.top, self.cy - a)
            self.cx = 0
        elif final in ("G", "`"):
            self.cx = min(self.cols - 1, a - 1)
        elif final == "d":
            self.cy = min(self.rows - 1, a - 1)
        elif final in ("H", "f"):
            row = n[0] if n[0] else 1
            col = n[1] if n[1] else 1
            self.cy = min(self.rows - 1, row - 1)
            self.cx = min(self.cols - 1, col - 1)
        elif final == "J":
            self._erase_display(n[0])
        elif final == "K":
            self._erase_line(n[0])
        elif final == "L":
            self._insert_lines(a)
        elif final == "M":
            self._delete_lines(a)
        elif final == "P":
            self._delete_chars(a)
        elif final == "@":
            self._insert_chars(a)
        elif final == "X":
            for i in range(self.cx, min(self.cols, self.cx + a)):
                self.grid[self.cy][i] = " "
        elif final == "S":
            for _ in range(a):
                self._scroll_up()
        elif final == "T":
            for _ in range(a):
                self._scroll_down()
        elif final == "r":
            top = (n[0] or 1) - 1
            bot = (n[1] or self.rows) - 1
            if 0 <= top < bot < self.rows:
                self.top, self.bot = top, bot
            else:
                self.top, self.bot = 0, self.rows - 1
            self.cx, self.cy = 0, self.top
        elif final == "s":
            self.saved = (self.cx, self.cy)
        elif final == "u":
            self.cx, self.cy = self.saved
            self._clamp()
        self.wrap_pending = False
        self._clamp()

    def _clamp(self):
        self.cx = max(0, min(self.cx, self.cols - 1))
        self.cy = max(0, min(self.cy, self.rows - 1))

    def _blank_row(self):
        return [" "] * self.cols

    def _index(self):
        """Line feed, honouring the scroll region."""
        if self.cy == self.bot:
            self._scroll_up()
        elif self.cy < self.rows - 1:
            self.cy += 1

    def _reverse_index(self):
        if self.cy == self.top:
            self._scroll_down()
        elif self.cy > 0:
            self.cy -= 1

    def _scroll_up(self):
        row = self.grid.pop(self.top)
        if self.top == 0 and self._alt is None:
            self.scrollback.append("".join(row).rstrip())
        self.grid.insert(self.bot, self._blank_row())

    def _scroll_down(self):
        self.grid.pop(self.bot)
        self.grid.insert(self.top, self._blank_row())

    def _insert_lines(self, count):
        if not self.top <= self.cy <= self.bot:
            return
        for _ in range(min(count, self.bot - self.cy + 1)):
            self.grid.pop(self.bot)
            self.grid.insert(self.cy, self._blank_row())

    def _delete_lines(self, count):
        if not self.top <= self.cy <= self.bot:
            return
        for _ in range(min(count, self.bot - self.cy + 1)):
            self.grid.pop(self.cy)
            self.grid.insert(self.bot, self._blank_row())

    def _delete_chars(self, count):
        row = self.grid[self.cy]
        for _ in range(min(count, self.cols - self.cx)):
            del row[self.cx]
            row.append(" ")

    def _insert_chars(self, count):
        row = self.grid[self.cy]
        for _ in range(min(count, self.cols - self.cx)):
            row.insert(self.cx, " ")
            del row[-1]

    def _erase_line(self, mode):
        row = self.grid[self.cy]
        if mode == 0:
            for i in range(self.cx, self.cols):
                row[i] = " "
        elif mode == 1:
            for i in range(0, min(self.cx + 1, self.cols)):
                row[i] = " "
        elif mode == 2:
            for i in range(self.cols):
                row[i] = " "

    def _erase_display(self, mode):
        if mode == 0:
            self._erase_line(0)
            for y in range(self.cy + 1, self.rows):
                self.grid[y] = self._blank_row()
        elif mode == 1:
            self._erase_line(1)
            for y in range(0, self.cy):
                self.grid[y] = self._blank_row()
        elif mode in (2, 3):
            if mode == 2 and self._alt is None:
                for row in self.grid:
                    line = "".join(row).rstrip()
                    if line:
                        self.scrollback.append(line)
            for y in range(self.rows):
                self.grid[y] = self._blank_row()
            if mode == 3:
                self.scrollback.clear()

    def _enter_alt(self):
        if self._alt is not None:
            return
        self._alt = (self.grid, self.cx, self.cy, self.top, self.bot)
        self.grid = [self._blank_row() for _ in range(self.rows)]
        self.cx = self.cy = 0
        self.top, self.bot = 0, self.rows - 1

    def _leave_alt(self):
        if self._alt is None:
            return
        self.grid, self.cx, self.cy, self.top, self.bot = self._alt
        self._alt = None
        self._clamp()
