#!/usr/bin/env python3
"""meshws - the small part of WebSocket (RFC 6455) Mesh Guest needs, standard library only.

Server side only: it answers a browser's upgrade request, then sends binary/text messages and reads the browser's
(masked) text/binary messages. Control frames (ping, pong, close) are handled here.
"""

import base64
import hashlib
import select
import socket
import struct
import threading
from typing import Optional, Tuple, Union

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WebSocketClosed(Exception):
    """The other side closed, or the connection broke."""


class WebSocketError(Exception):
    """The other side broke the protocol."""


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def is_upgrade(headers) -> bool:
    return ("websocket" in (headers.get("Upgrade") or "").lower()
            and "upgrade" in (headers.get("Connection") or "").lower()
            and (headers.get("Sec-WebSocket-Version") or "") == "13"
            and bool(headers.get("Sec-WebSocket-Key")))


def handshake_response(key: str) -> bytes:
    return (f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n").encode("ascii")


def encode_frame(opcode: int, payload: bytes, mask_key: Optional[bytes] = None) -> bytes:
    """A single final frame. Servers send unmasked; mask_key exists for tests (a client)."""
    first = 0x80 | opcode
    length = len(payload)
    mask_bit = 0x80 if mask_key else 0
    if length < 126:
        head = struct.pack("!BB", first, mask_bit | length)
    elif length < 65536:
        head = struct.pack("!BBH", first, mask_bit | 126, length)
    else:
        head = struct.pack("!BBQ", first, mask_bit | 127, length)
    if mask_key:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        return head + mask_key + payload
    return head + payload


class WebSocket:
    def __init__(self, sock: socket.socket, max_message: int = 1 << 20, send_timeout: float = 5.0) -> None:
        self.sock = sock
        self.max_message = max_message
        self.send_timeout = send_timeout
        self.buf = bytearray()
        self.parts = bytearray()
        self.part_op = 0
        self.closed = False
        self.send_lock = threading.Lock()

    # ---- sending
    def _send(self, data: bytes) -> None:
        if self.closed:
            raise WebSocketClosed("closed")
        with self.send_lock:
            try:
                self.sock.settimeout(self.send_timeout)
                self.sock.sendall(data)
            except (OSError, ValueError) as exc:     # includes a send that timed out: the other side is not keeping up
                self.closed = True
                raise WebSocketClosed(str(exc)) from None

    def send_binary(self, data: bytes) -> None:
        self._send(encode_frame(OP_BINARY, data))

    def send_text(self, text: str) -> None:
        self._send(encode_frame(OP_TEXT, text.encode("utf-8")))

    def ping(self, data: bytes = b"") -> None:
        self._send(encode_frame(OP_PING, data[:125]))

    def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed:
            return
        try:
            self._send(encode_frame(OP_CLOSE, struct.pack("!H", code) + reason.encode("utf-8")[:100]))
        except WebSocketClosed:
            pass
        self.closed = True

    # ---- receiving
    def _frame(self) -> Optional[Tuple[bool, int, bytes]]:
        """One whole frame from the buffer, or None when more bytes are needed."""
        buf = self.buf
        if len(buf) < 2:
            return None
        fin, opcode = bool(buf[0] & 0x80), buf[0] & 0x0F
        if buf[0] & 0x70:
            raise WebSocketError("reserved bits set")
        masked, length, pos = bool(buf[1] & 0x80), buf[1] & 0x7F, 2
        if length == 126:
            if len(buf) < 4:
                return None
            length, pos = struct.unpack("!H", bytes(buf[2:4]))[0], 4
        elif length == 127:
            if len(buf) < 10:
                return None
            length, pos = struct.unpack("!Q", bytes(buf[2:10]))[0], 10
        if not masked:
            raise WebSocketError("client frames must be masked")
        if length > self.max_message or (opcode >= 8 and (length > 125 or not fin)):
            raise WebSocketError("frame too large")
        if len(buf) < pos + 4 + length:
            return None
        key = bytes(buf[pos:pos + 4])
        payload = bytearray(buf[pos + 4:pos + 4 + length])
        for i in range(length):
            payload[i] ^= key[i & 3]
        del buf[:pos + 4 + length]
        return fin, opcode, bytes(payload)

    def recv(self, timeout: Optional[float] = None) -> Optional[Tuple[str, Union[str, bytes]]]:
        """-> ("text", str) | ("binary", bytes), or None when nothing arrived within the timeout.
        Raises WebSocketClosed when the other side closes or the connection breaks, WebSocketError on a protocol fault."""
        while True:
            frame = self._frame()
            if frame is None:
                if self.closed:
                    raise WebSocketClosed("closed")
                try:
                    ready, _, _ = select.select([self.sock], [], [], timeout)
                except (OSError, ValueError):
                    self.closed = True
                    raise WebSocketClosed("socket closed") from None
                if not ready:
                    return None
                try:
                    chunk = self.sock.recv(65536)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError as exc:
                    self.closed = True
                    raise WebSocketClosed(str(exc)) from None
                if not chunk:
                    self.closed = True
                    raise WebSocketClosed("the other side went away")
                self.buf += chunk
                continue
            fin, opcode, payload = frame
            if opcode == OP_PING:
                try:
                    self._send(encode_frame(OP_PONG, payload))
                except WebSocketClosed:
                    raise
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                self.close(1000)
                raise WebSocketClosed("closed by the other side")
            if opcode in (OP_TEXT, OP_BINARY):
                if self.parts:
                    raise WebSocketError("new message inside a fragmented one")
                if fin:
                    return self._deliver(opcode, payload)
                self.part_op, self.parts = opcode, bytearray(payload)
                continue
            if opcode == OP_CONT:
                if not self.parts and not self.part_op:
                    raise WebSocketError("continuation without a start")
                self.parts += payload
                if len(self.parts) > self.max_message:
                    raise WebSocketError("message too large")
                if fin:
                    data, op = bytes(self.parts), self.part_op
                    self.parts, self.part_op = bytearray(), 0
                    return self._deliver(op, data)
                continue
            raise WebSocketError(f"unknown opcode {opcode}")

    @staticmethod
    def _deliver(opcode: int, payload: bytes) -> Tuple[str, Union[str, bytes]]:
        if opcode == OP_TEXT:
            try:
                return "text", payload.decode("utf-8")
            except UnicodeDecodeError:
                raise WebSocketError("text message is not UTF-8") from None
        return "binary", payload
