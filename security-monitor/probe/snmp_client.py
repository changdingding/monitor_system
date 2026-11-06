"""最小 SNMPv2c GET / GETNEXT，供门店采集器使用，不依赖第三方库。"""
from __future__ import annotations

import socket
from typing import List, Optional, Tuple


def snmp_get(host: str, community: str, oid: str, port: int = 161, timeout: float = 2.0):
    rows = _exchange(host, community, 0xA0, [oid], port, timeout)
    if not rows:
        return None
    return rows[0][1]


def snmp_walk(host: str, community: str, base_oid: str, port: int = 161, timeout: float = 2.0, limit: int = 256):
    current = base_oid
    seen = set()
    found = []
    prefix = base_oid + "."
    for _ in range(limit):
        rows = _exchange(host, community, 0xA1, [current], port, timeout)
        if not rows:
            break
        oid, value = rows[0]
        if not oid or oid in seen or not (oid == base_oid or oid.startswith(prefix)):
            break
        if value is None:
            break
        seen.add(oid)
        found.append((oid, value))
        current = oid
    return found


def _exchange(host, community, pdu_tag, oids, port, timeout) -> Optional[List[Tuple[str, object]]]:
    packet = _encode_message(community, pdu_tag, oids)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(packet, (host, int(port)))
        data, _ = sock.recvfrom(65535)
    except (OSError, socket.timeout):
        return None
    finally:
        sock.close()
    try:
        return _decode_message(data)
    except Exception:
        return None


def _encode_message(community: str, pdu_tag: int, oids: list) -> bytes:
    request_id = 1
    varbinds = b"".join(_encode_sequence(_encode_oid(oid) + _encode_null()) for oid in oids)
    pdu_body = _encode_int(request_id) + _encode_int(0) + _encode_int(0) + _encode_sequence(varbinds)
    pdu = bytes([pdu_tag]) + _encode_length(len(pdu_body)) + pdu_body
    body = _encode_int(1) + _encode_octet(community.encode("utf-8")) + pdu
    return _encode_sequence(body)


def _encode_length(length: int) -> bytes:
    if length < 128:
        return bytes([length])
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _encode_tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _encode_length(len(value)) + value


def _encode_int(number: int) -> bytes:
    if number == 0:
        raw = b"\x00"
    else:
        raw = int(number).to_bytes((int(number).bit_length() + 8) // 8, "big", signed=True)
        while len(raw) > 1 and (
            (raw[0] == 0 and raw[1] < 0x80) or (raw[0] == 0xFF and raw[1] >= 0x80)
        ):
            raw = raw[1:]
    return _encode_tlv(0x02, raw)


def _encode_octet(value: bytes) -> bytes:
    return _encode_tlv(0x04, value)


def _encode_null() -> bytes:
    return _encode_tlv(0x05, b"")


def _encode_oid(oid: str) -> bytes:
    parts = [int(item) for item in oid.strip(".").split(".") if item != ""]
    if len(parts) < 2:
        raise ValueError(oid)
    body = bytes([parts[0] * 40 + parts[1]])
    for part in parts[2:]:
        if part < 0:
            raise ValueError(oid)
        stack = [part & 0x7F]
        part >>= 7
        while part:
            stack.append((part & 0x7F) | 0x80)
            part >>= 7
        body += bytes(reversed(stack))
    return _encode_tlv(0x06, body)


def _encode_sequence(content: bytes) -> bytes:
    return _encode_tlv(0x30, content)


def _read_tlv(buf: bytes, index: int):
    tag = buf[index]
    index += 1
    length = buf[index]
    index += 1
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(buf[index:index + count], "big")
        index += count
    value = buf[index:index + length]
    return tag, value, index + length


def _decode_message(data: bytes):
    tag, value, _ = _read_tlv(data, 0)
    if tag != 0x30:
        return None
    index = 0
    _, _, index = _read_tlv(value, index)  # version
    _, _, index = _read_tlv(value, index)  # community
    pdu_tag, pdu, _ = _read_tlv(value, index)
    if pdu_tag != 0xA2:
        return None
    cursor = 0
    _, _, cursor = _read_tlv(pdu, cursor)  # request-id
    _, err_raw, cursor = _read_tlv(pdu, cursor)
    if int.from_bytes(err_raw, "big", signed=True) != 0:
        return None
    _, _, cursor = _read_tlv(pdu, cursor)  # error-index
    _, binds, _ = _read_tlv(pdu, cursor)
    rows = []
    pos = 0
    while pos < len(binds):
        _, pair, pos = _read_tlv(binds, pos)
        inner = 0
        oid_tag, oid_raw, inner = _read_tlv(pair, inner)
        val_tag, val_raw, _ = _read_tlv(pair, inner)
        oid = _decode_oid(oid_raw) if oid_tag == 0x06 else ""
        rows.append((oid, _decode_value(val_tag, val_raw)))
    return rows


def _decode_oid(data: bytes) -> str:
    if not data:
        return ""
    parts = [str(data[0] // 40), str(data[0] % 40)]
    index = 1
    while index < len(data):
        value = 0
        while index < len(data):
            byte = data[index]
            index += 1
            value = (value << 7) | (byte & 0x7F)
            if not (byte & 0x80):
                break
        parts.append(str(value))
    return ".".join(parts)


def _decode_value(tag: int, data: bytes):
    if tag in (0x02, 0x41, 0x42, 0x43, 0x46):
        return int.from_bytes(data or b"\x00", "big", signed=(tag == 0x02))
    if tag == 0x04:
        try:
            return data.decode("utf-8")
        except Exception:
            return data.decode("latin-1", errors="replace")
    if tag in (0x05, 0x80, 0x81, 0x82):
        return None
    if tag == 0x06:
        return _decode_oid(data)
    return data.hex()
