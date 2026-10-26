"""Build-time closure checks for optional plugin branding; standard library only."""
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path, PurePosixPath

ROLES = ("logo", "logoDark", "composerIcon", "composerIconDark")
MAX_ICON = 1024 * 1024


def declared_assets(identity):
    ui = identity.get("extensions", {}).get("com.openai", {}).get("interface", {})
    for role in ROLES:
        value = ui.get(role)
        if value is None:
            continue
        if not isinstance(value, str) or not value.startswith("./assets/") or "\\" in value:
            raise ValueError("invalidPresentationAsset")
        path = PurePosixPath(value[2:])
        if any(part in ("", ".", "..") for part in value[2:].split("/")) or path.suffix not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
            raise ValueError("invalidPresentationAsset")
        yield str(path)


def validate_icon(path, data):
    if not data or len(data) > MAX_ICON:
        raise ValueError("invalidPresentationAsset")
    suffix = PurePosixPath(path).suffix
    dimensions = None
    if suffix == ".svg":
        if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
            raise ValueError("invalidPresentationAsset")
        node = ET.fromstring(data)
        nodes = list(node.iter())
        if len(nodes) > 4096 or node.tag.split("}")[-1] != "svg":
            raise ValueError("invalidPresentationAsset")
        for item in nodes:
            if item.tag.split("}")[-1] in {"script", "foreignObject", "image", "feImage", "text", "use"} or any(key.split("}")[-1] in {"href", "src"} or key.split("}")[-1].startswith("on") for key in item.attrib):
                raise ValueError("invalidPresentationAsset")
        width, height = node.get("width"), node.get("height")
        if width is not None and height is not None and re.fullmatch(r"[0-9.]+", width) and re.fullmatch(r"[0-9.]+", height):
            dimensions = float(width), float(height)
        else:
            box = [float(part) for part in node.get("viewBox", "").replace(",", " ").split()]
            if len(box) == 4:
                dimensions = box[2], box[3]
    elif suffix == ".png":
        if data[:8] != b'\x89PNG\r\n\x1a\n' or len(data) < 24:
            raise ValueError("invalidPresentationAsset")
        dimensions = struct.unpack(">II", data[16:24])
        offset, ended = 8, False
        while offset + 12 <= len(data):
            length = struct.unpack(">I", data[offset:offset + 4])[0]
            end = offset + length + 12
            if end > len(data) or zlib.crc32(data[offset + 4:end - 4]) & 0xffffffff != struct.unpack(">I", data[end - 4:end])[0]:
                raise ValueError("invalidPresentationAsset")
            kind = data[offset + 4:offset + 8]
            offset = end
            if kind == b'IEND':
                ended = offset == len(data)
                break
        if not ended:
            raise ValueError("invalidPresentationAsset")
    elif suffix in (".jpg", ".jpeg"):
        if not data.startswith(b'\xff\xd8'):
            raise ValueError("invalidPresentationAsset")
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 255:
                break
            marker = data[offset + 1]
            if marker == 255:
                offset += 1
                continue
            size = int.from_bytes(data[offset + 2:offset + 4], 'big')
            if size < 2 or offset + 2 + size > len(data):
                break
            if marker in (0xc0, 0xc1, 0xc2):
                if size < 8:
                    break
                dimensions = struct.unpack('>HH', data[offset + 5:offset + 9])[::-1]
                break
            offset += 2 + size
        if not data.endswith(b'\xff\xd9'):
            raise ValueError("invalidPresentationAsset")
    elif suffix == ".webp":
        if data[:4] != b'RIFF' or data[8:12] != b'WEBP':
            raise ValueError("invalidPresentationAsset")
        if len(data) < 30 or int.from_bytes(data[4:8], 'little') + 8 != len(data):
            raise ValueError("invalidPresentationAsset")
        kind = data[12:16]
        if kind == b'VP8X':
            dimensions = (1 + int.from_bytes(data[24:27], 'little'), 1 + int.from_bytes(data[27:30], 'little'))
        elif kind == b'VP8L' and data[20] == 0x2f:
            bits = int.from_bytes(data[21:25], 'little')
            dimensions = (1 + (bits & 0x3fff), 1 + ((bits >> 14) & 0x3fff))
        elif kind == b'VP8 ' and data[23:26] == b'\x9d\x01\x2a':
            dimensions = tuple(value & 0x3fff for value in struct.unpack('<HH', data[26:30]))
    if not dimensions or dimensions[0] != dimensions[1] or not 48 <= dimensions[0] <= 4096:
        raise ValueError("invalidPresentationAsset")


def validate_asset_files(files, identity):
    for relative in declared_assets(identity):
        if relative not in files:
            raise ValueError("missingPresentationAsset")
        validate_icon(relative, files[relative])


def validate_assets(root: Path, identity):
    for relative in declared_assets(identity):
        path = root
        for part in PurePosixPath(relative).parts:
            path = path / part
            if path.is_symlink() or path.is_junction():
                raise ValueError("reparseSource")
        if not path.is_file() or path.stat().st_size > MAX_ICON:
            raise ValueError("missingPresentationAsset")
        validate_icon(relative, path.read_bytes())
