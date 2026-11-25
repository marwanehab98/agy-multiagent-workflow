"""Write frames as PNG files with only zlib and struct, upscaled so each pixel is easy to see."""
import struct
import zlib

from art import H, RGB, W


def _chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def png_bytes(pixels, scale=6, caption_rgb=None):
    """An indexed-colour PNG of a W x H frame (palette indices), each pixel drawn as scale x scale."""
    rows = []
    for y in range(H):
        row = bytearray()
        for x in range(W):
            row += bytes([pixels[y * W + x]]) * scale
        line = b"\x00" + bytes(row)
        rows.extend([line] * scale)
    raw = b"".join(rows)
    plte = b"".join(bytes(rgb) for rgb in RGB)
    ihdr = struct.pack(">IIBBBBB", W * scale, H * scale, 8, 3, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"PLTE", plte)
            + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))


def write_png(path, pixels, scale=6):
    with open(path, "wb") as f:
        f.write(png_bytes(pixels, scale))
