"""A small QR Code encoder (model 2, byte mode, versions 1 to 10) with nothing but the standard library.

`meshd pair` uses it so a new computer needs no extra package (no `qrencode`) to show a code the Mesh app can scan.
Versions 1 to 10 hold up to 271 bytes at the lowest error correction, far more than a pairing link needs.

    matrix = encode(b"mesh://pair?...")                  # list of rows of bools, True = dark
    print(render_terminal(matrix, scale=2))              # black on white, whatever the terminal's own colours
    write_png("pair.png", matrix)                        # for a terminal that cannot draw it

The tables and the layout rules are the ones of ISO/IEC 18004; the tests compare the result with independent encoders
(for every version, level and mask) and decode it with a real scanner library.
"""

import struct
import zlib
from typing import List, Optional, Sequence

LOW, MEDIUM, QUARTILE, HIGH = 0, 1, 2, 3
_FORMAT_BITS = {LOW: 1, MEDIUM: 0, QUARTILE: 3, HIGH: 2}
MAX_VERSION = 10

# Index 0 is padding. Versions 1 to 10, per error correction level.
_ECC_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28),
)
_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8),
)


class QrError(ValueError):
    pass


def _raw_modules(version: int) -> int:
    result = (16 * version + 128) * version + 64
    if version >= 2:
        n = version // 7 + 2
        result -= (25 * n - 10) * n - 55
        if version >= 7:
            result -= 36
    return result


def data_capacity(version: int, ecc: int) -> int:
    """How many data codewords (bytes) a version holds at this level, before the mode and length header."""
    return _raw_modules(version) // 8 - _ECC_PER_BLOCK[ecc][version] * _BLOCKS[ecc][version]


def _alignment_positions(version: int) -> List[int]:
    if version == 1:
        return []
    n = version // 7 + 2
    step = (version * 4 + n * 2 + 1) // (n * 2 - 2) * 2
    return [6] + [(version * 4 + 10) - i * step for i in range(n - 2, -1, -1)]


# ---- Reed-Solomon over GF(256), polynomial 0x11D

def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_generator(degree: int) -> List[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: Sequence[int], generator: Sequence[int]) -> List[int]:
    result = [0] * len(generator)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(generator):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _codewords(payload: bytes, version: int, ecc: int) -> List[int]:
    capacity = data_capacity(version, ecc)
    bits: List[int] = []

    def put(value: int, count: int) -> None:
        bits.extend((value >> i) & 1 for i in range(count - 1, -1, -1))

    put(0b0100, 4)                                   # byte mode
    put(len(payload), 8 if version <= 9 else 16)
    for b in payload:
        put(b, 8)
    put(0, min(4, capacity * 8 - len(bits)))         # terminator
    put(0, -len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < capacity:
        data.append(pad)
        pad ^= 0xEC ^ 0x11
    # split into blocks, add error correction to each, and interleave
    blocks_n, ecc_len = _BLOCKS[ecc][version], _ECC_PER_BLOCK[ecc][version]
    raw = _raw_modules(version) // 8
    short_blocks = blocks_n - raw % blocks_n
    short_len = raw // blocks_n
    generator = _rs_generator(ecc_len)
    blocks, k = [], 0
    for i in range(blocks_n):
        length = short_len - ecc_len + (0 if i < short_blocks else 1)
        chunk = data[k:k + length]
        k += length
        remainder = _rs_remainder(chunk, generator)
        if i < short_blocks:
            chunk = chunk + [0]
        blocks.append(chunk + remainder)
    out = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_len - ecc_len or j >= short_blocks:    # skip the padding slot of a short block
                out.append(block[i])
    return out


class _Grid:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = version * 4 + 17
        self.m = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]

    def set_fn(self, x: int, y: int, dark: bool) -> None:
        self.m[y][x] = dark
        self.fn[y][x] = True

    def draw_function_patterns(self) -> None:
        size = self.size
        for i in range(size):
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set_fn(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        positions = _alignment_positions(self.version)
        last = len(positions) - 1
        for i, cy in enumerate(positions):
            for j, cx in enumerate(positions):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0, 0)      # reserve the areas; the real bits come after the mask is chosen
        self.draw_version()

    def draw_format(self, ecc: int, mask: int) -> None:
        size = self.size
        data = _FORMAT_BITS[ecc] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 != 0
        for i in range(0, 6):
            self.set_fn(8, i, bit(i))
        self.set_fn(8, 7, bit(6))
        self.set_fn(8, 8, bit(7))
        self.set_fn(7, 8, bit(8))
        for i in range(9, 15):
            self.set_fn(14 - i, 8, bit(i))
        for i in range(0, 8):
            self.set_fn(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_fn(8, size - 15 + i, bit(i))
        self.set_fn(8, size - 8, True)

    def draw_version(self) -> None:
        if self.version < 7:
            return
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.version << 12 | rem
        for i in range(18):
            dark = (bits >> i) & 1 != 0
            a, b = self.size - 11 + i % 3, i // 3
            self.set_fn(a, b, dark)
            self.set_fn(b, a, dark)

    def place(self, data: Sequence[int]) -> None:
        size, i = self.size, 0
        for right in range(size - 1, 0, -2):
            if right <= 6:
                right -= 1
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = (right + 1) & 2 == 0
                    y = size - 1 - vert if upward else vert
                    if not self.fn[y][x] and i < len(data) * 8:
                        self.m[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 != 0
                        i += 1

    def apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                invert = (
                    (x + y) % 2 == 0, y % 2 == 0, x % 3 == 0, (x + y) % 3 == 0, (x // 3 + y // 2) % 2 == 0,
                    x * y % 2 + x * y % 3 == 0, (x * y % 2 + x * y % 3) % 2 == 0, ((x + y) % 2 + x * y % 3) % 2 == 0,
                )[mask]
                if invert and not self.fn[y][x]:
                    self.m[y][x] = not self.m[y][x]

    def penalty(self) -> int:
        size, m, result = self.size, self.m, 0

        def finder_count(h: List[int]) -> int:
            n = h[1]
            core = n > 0 and h[2] == n and h[3] == n * 3 and h[4] == n and h[5] == n
            return (1 if core and h[0] >= n * 4 and h[6] >= n else 0) + (1 if core and h[6] >= n * 4 and h[0] >= n else 0)

        def add_history(length: int, h: List[int]) -> None:
            if h[0] == 0:
                length += size
            h.pop()
            h.insert(0, length)

        def line_score(line: Sequence[bool]) -> int:
            score, color, run, h = 0, False, 0, [0] * 7
            for v in line:
                if v == color:
                    run += 1
                    if run == 5:
                        score += 3
                    elif run > 5:
                        score += 1
                else:
                    add_history(run, h)
                    if not color:
                        score += finder_count(h) * 40
                    color, run = v, 1
            if color:
                add_history(run, h)
                run = 0
            run += size
            add_history(run, h)
            return score + finder_count(h) * 40

        for y in range(size):
            result += line_score(m[y])
        for x in range(size):
            result += line_score([m[y][x] for y in range(size)])
        for y in range(size - 1):
            for x in range(size - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    result += 3
        dark = sum(row.count(True) for row in m)
        total = size * size
        result += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10
        return result


def encode(payload: bytes, ecc: int = LOW, mask: Optional[int] = None, min_version: int = 1) -> List[List[bool]]:
    """The smallest version that holds the payload (not below min_version). Pass a mask (0 to 7) to force it, otherwise the best one is used."""
    for version in range(min_version, MAX_VERSION + 1):
        header = 4 + (8 if version <= 9 else 16)
        if header + len(payload) * 8 <= data_capacity(version, ecc) * 8:
            break
    else:
        raise QrError(f"{len(payload)} bytes do not fit a version {MAX_VERSION} code at this level (the limit is {data_capacity(MAX_VERSION, ecc) - 3})")
    grid = _Grid(version)
    grid.draw_function_patterns()
    grid.place(_codewords(payload, version, ecc))
    if mask is None:
        best, mask = None, 0
        for candidate in range(8):
            grid.apply_mask(candidate)
            grid.draw_format(ecc, candidate)
            score = grid.penalty()
            if best is None or score < best:
                best, mask = score, candidate
            grid.apply_mask(candidate)          # undo (the mask is its own inverse)
    grid.apply_mask(mask)
    grid.draw_format(ecc, mask)
    return grid.m


def render_terminal(matrix: List[List[bool]], scale: int = 1, border: int = 2, color: bool = True) -> str:
    """The code as text, black on white whatever the terminal's own colours are (a scanner needs dark modules on a light ground).

    scale 1: one column per module, two modules per row (half blocks). scale 2: two columns per module, one module per row: twice
    as wide and easier to scan from further away. With color=False the dark modules are '#' and the light ones ' '."""
    size = len(matrix)
    total = size + 2 * border

    def dark(x: int, y: int) -> bool:
        return 0 <= x - border < size and 0 <= y - border < size and matrix[y - border][x - border]

    reset = "\x1b[0m"
    black, white = "\x1b[38;5;16;48;5;16m", "\x1b[38;5;231;48;5;231m"
    lines = []
    if scale >= 2:
        for y in range(total):
            if color:
                lines.append("".join((black if dark(x, y) else white) + "  " for x in range(total)) + reset)
            else:
                lines.append("".join("##" if dark(x, y) else "  " for x in range(total)))
    else:
        for y in range(0, total, 2):
            if color:
                row = []
                for x in range(total):
                    top, bottom = dark(x, y), dark(x, y + 1)
                    row.append(f"\x1b[38;5;{16 if top else 231};48;5;{16 if bottom else 231}m▀")
                lines.append("".join(row) + reset)
            else:
                lines.append("".join({(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}[(dark(x, y), dark(x, y + 1))]
                                     for x in range(total)))
    return "\n".join(lines)


def write_png(path: str, matrix: List[List[bool]], module: int = 10, border: int = 4) -> None:
    """A grayscale PNG of the code (dark modules black on white), for a terminal that cannot show it."""
    size = len(matrix)
    total = (size + 2 * border) * module
    rows = []
    for py in range(total):
        y = py // module - border
        row = bytearray([0])        # filter type none
        for px in range(total):
            x = px // module - border
            row.append(0 if 0 <= x < size and 0 <= y < size and matrix[y][x] else 255)
        rows.append(bytes(row))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", total, total, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
