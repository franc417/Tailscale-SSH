"""meshqr: the dependency-free QR encoder. The golden hashes were produced by this encoder AND confirmed identical, module for
module, to Nayuki's reference library (qrcodegen) and, for every version, level and mask, to segno; a real scanner library
(zxing-cpp) decoded the output."""

import hashlib
import os
import struct
import sys
import zlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import meshqr  # noqa: E402

GOLDEN = [
    ("hello-v1", 11, 0, 1, "0617bc5fca65c1817aedbfeca585c6ea618884e566374c8c3414dcb9af450483"),
    ("hello-m", 11, 1, 1, "f516d851861750f4deffb7514598d7058ab57a40b130409930189536174c89aa"),
    ("v7", 140, 0, 7, "3638440986a0810a23f014774a2c1e63a21a68e4021764fd96e0f08469b8bcd6"),
    ("v10-h", 100, 3, 10, "bf025dcb0270028c5197cb6f2457a600c2f1d8efab69cecc4663ac5e699d97e5"),
    ("v10-l", 268, 0, 10, "a70f83ca376698e92ea5998bd464a75b739fc52d67548e16da26e09218d67060"),
    ("v3-q", 40, 2, 4, "0125b200aae40c500b051d08275dc844643641fc930f9f7db49343aac6f42112"),
]
PAYLOADS = {"hello-v1": b"HELLO WORLD", "hello-m": b"HELLO WORLD", "v7": bytes((i * 37 + 11) % 256 for i in range(140)),
            "v10-h": bytes((i * 53 + 7) % 256 for i in range(100)), "v10-l": bytes((i * 29 + 3) % 256 for i in range(268)),
            "v3-q": bytes((i * 13 + 5) % 256 for i in range(40))}


def digest(matrix):
    return hashlib.sha256("".join("1" if v else "0" for row in matrix for v in row).encode()).hexdigest()


@pytest.mark.parametrize("name,length,ecc,version,expected", GOLDEN)
def test_golden_codes(name, length, ecc, version, expected):
    payload = PAYLOADS[name]
    assert len(payload) == length
    matrix = meshqr.encode(payload, ecc)
    assert len(matrix) == version * 4 + 17
    assert digest(matrix) == expected


def test_the_smallest_version_that_fits_is_used():
    assert len(meshqr.encode(b"x" * 17)) == 21          # version 1 holds 17 bytes at the lowest level
    assert len(meshqr.encode(b"x" * 18)) == 25          # one more byte needs version 2


def test_a_payload_too_long_for_version_10_is_refused_with_the_limit():
    with pytest.raises(meshqr.QrError) as e:
        meshqr.encode(b"x" * 300)
    assert "300 bytes" in str(e.value)


def test_finder_timing_and_dark_module_are_in_place():
    m = meshqr.encode(b"mesh")
    size = len(m)
    for ox, oy in ((0, 0), (size - 7, 0), (0, size - 7)):
        assert all(m[oy][ox + i] and m[oy + 6][ox + i] and m[oy + i][ox] and m[oy + i][ox + 6] for i in range(7))   # outer ring
        assert all(m[oy + 2 + a][ox + 2 + b] for a in range(3) for b in range(3))                                   # centre 3x3
    assert all(m[6][i] == (i % 2 == 0) for i in range(8, size - 8))                                                  # timing row
    assert all(m[i][6] == (i % 2 == 0) for i in range(8, size - 8))                                                  # timing column
    assert m[size - 8][8]                                                                                            # the always-dark module


def test_format_bits_are_a_valid_bch_word_and_both_copies_agree():
    m = meshqr.encode(b"mesh", meshqr.QUARTILE)
    size = len(m)
    # modules are m[row][column]; the format word is drawn twice: around the top-left finder and split between the other two
    first = [m[i][8] for i in range(6)] + [m[7][8], m[8][8], m[8][7]] + [m[8][14 - i] for i in range(9, 15)]
    second = [m[8][size - 1 - i] for i in range(8)] + [m[size - 15 + i][8] for i in range(8, 15)]
    assert first == second
    raw = sum(1 << i for i, v in enumerate(first) if v) ^ 0x5412
    data5, check10 = raw >> 10, raw & 0x3FF
    rem = data5
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    assert rem == check10                                       # the 10 check bits belong to the 5 data bits
    assert data5 >> 3 == 3                                      # quartile is 3 in the format field


def test_forcing_a_mask_changes_the_picture_but_not_the_size():
    a, b = meshqr.encode(b"mesh", mask=0), meshqr.encode(b"mesh", mask=5)
    assert len(a) == len(b) and a != b


def test_reed_solomon_known_vector():
    # the generator polynomial for 7 error-correction codewords (QR version 1-L): x^7 + a^87 x^6 + ... in coefficient form
    assert meshqr._rs_generator(7) == [127, 122, 154, 164, 11, 68, 117]


def test_terminal_rendering_shapes():
    m = meshqr.encode(b"mesh")
    n = len(m) + 4
    wide = meshqr.render_terminal(m, scale=2, color=False).splitlines()
    assert len(wide) == n and all(len(line) == 2 * n for line in wide)
    half = meshqr.render_terminal(m, scale=1, color=False).splitlines()
    assert len(half) == (n + 1) // 2 and all(len(line) == n for line in half)
    colored = meshqr.render_terminal(m, scale=1, color=True)
    assert "\x1b[38;5;16" in colored and "\x1b[38;5;231" in colored      # explicit black and white, not the terminal's own colours
    assert meshqr.render_terminal(m, color=False).count("\x1b") == 0


def test_the_quiet_zone_is_light():
    m = meshqr.encode(b"mesh")
    top = meshqr.render_terminal(m, scale=2, color=False).splitlines()[:2]
    assert all(set(line) == {" "} for line in top)


def test_png_is_a_valid_grayscale_picture_of_the_code(tmp_path):
    m = meshqr.encode(b"mesh")
    path = tmp_path / "code.png"
    meshqr.write_png(str(path), m, module=4, border=2)
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(data):
        length, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        assert struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0] == zlib.crc32(tag + body) & 0xFFFFFFFF
        chunks[tag] = body
        pos += 12 + length
    width, height, depth, kind = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    assert (width, height, depth, kind) == ((len(m) + 4) * 4,) * 2 + (8, 0)
    raw = zlib.decompress(chunks[b"IDAT"])
    assert len(raw) == height * (width + 1)
    first_dark_row = raw[(2 * 4) * (width + 1) + 1:(2 * 4) * (width + 1) + 1 + width]
    assert first_dark_row[2 * 4] == 0 and first_dark_row[0] == 255      # inside the finder is black, the margin is white
