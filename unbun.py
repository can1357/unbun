#!/usr/bin/env python3
"""
Minimal Bun standalone bundle extractor.

Reads the StandaloneModuleGraph trailer embedded in Bun executables,
dumps JavaScript entries to disk, and optionally runs Prettier.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

TRAILER = b"\n---- Bun! ----\n"
OFFSETS_STRUCT_SIZE = 32  # u64 + u64 + u32 + u64
MODULE_STRUCT_SIZE = 36


class ExtractionError(RuntimeError):
    pass


def decode_pointer(raw: int) -> Tuple[int, int]:
    """Split packed 64-bit pointer into (offset, length)."""
    offset = raw & 0xFFFFFFFF
    length = (raw >> 32) & 0xFFFFFFFF
    return offset, length


def slice_from(blob: memoryview, pointer: Tuple[int, int]) -> bytes:
    offset, length = pointer
    if length == 0:
        return b""
    end = offset + length
    if offset < 0 or end > len(blob):
        raise ExtractionError("Module graph pointer out of range")
    data = bytes(blob[offset:end])
    if data.endswith(b"\x00"):
        data = data[:-1]
    return data


def find_module_graph(buffer: bytes) -> Tuple[memoryview, int]:
    trailer_idx = buffer.rfind(TRAILER)
    if trailer_idx == -1:
        raise ExtractionError("Module graph trailer not found")
    offsets_start = trailer_idx - OFFSETS_STRUCT_SIZE
    if offsets_start < 0:
        raise ExtractionError("Module graph offsets out of range")

    # Offsets struct layout: byte_count (u64), modules_ptr (u64), entry_point_id (u32), compile_ptr (u64)
    byte_count = int.from_bytes(buffer[offsets_start : offsets_start + 8], "little")
    if byte_count <= 0 or byte_count > len(buffer):
        raise ExtractionError("Invalid module graph byte count")

    module_graph_start = offsets_start - byte_count
    if module_graph_start < 0:
        raise ExtractionError("Module graph start before file")

    graph = memoryview(buffer)[module_graph_start:offsets_start]
    modules_raw = int.from_bytes(buffer[offsets_start + 8 : offsets_start + 16], "little")
    return graph, modules_raw


def iter_modules(graph: memoryview, modules_raw: int) -> Iterable[Tuple[str, bytes, int, int, int, int]]:
    modules_offset, modules_length = decode_pointer(modules_raw)
    if modules_length == 0 or modules_length % MODULE_STRUCT_SIZE != 0:
        return

    if modules_offset + modules_length > len(graph):
        raise ExtractionError("Modules table exceeds module graph bounds")

    count = modules_length // MODULE_STRUCT_SIZE
    table = graph[modules_offset : modules_offset + modules_length]

    for idx in range(count):
        base = idx * MODULE_STRUCT_SIZE
        name_ptr = (int.from_bytes(table[base : base + 4], "little"),
                    int.from_bytes(table[base + 4 : base + 8], "little"))
        contents_ptr = (int.from_bytes(table[base + 8 : base + 12], "little"),
                        int.from_bytes(table[base + 12 : base + 16], "little"))
        sourcemap_ptr = (int.from_bytes(table[base + 16 : base + 20], "little"),
                         int.from_bytes(table[base + 20 : base + 24], "little"))
        bytecode_ptr = (int.from_bytes(table[base + 24 : base + 28], "little"),
                        int.from_bytes(table[base + 28 : base + 32], "little"))

        encoding = table[base + 32]
        loader = table[base + 33]
        module_format = table[base + 34]
        side = table[base + 35]

        name = slice_from(graph, name_ptr).decode("utf-8", errors="replace")
        contents = slice_from(graph, contents_ptr)
        yield name, contents, encoding, loader, module_format, side


JS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


def should_extract(name: str, encoding: int, loader: int) -> bool:
    if encoding == 0:  # binary
        return False
    if name.endswith(JS_EXTENSIONS):
        return True
    # Fallback: treat loaders 1/2 (JS/TS) as JS even without extension.
    return loader in (1, 2)


def run_prettier(path: Path) -> None:
    try:
        subprocess.run(
            [run_prettier.prettier_bin, "--log-level=error", "--parser=babel", "--write", str(path)],
            check=True,
        )
    except FileNotFoundError as exc:
        raise ExtractionError(f"Prettier executable not found: {run_prettier.prettier_bin}") from exc
    except subprocess.CalledProcessError as exc:
        raise ExtractionError(f"Prettier failed on {path.name}") from exc


run_prettier.prettier_bin = "prettier"


def extract(binary: Path, output: Path, prettify: bool) -> None:
    buf = binary.read_bytes()
    graph, modules_raw = find_module_graph(buf)

    bundles: List[Tuple[str, bytes]] = []
    for name, contents, encoding, loader, _, _ in iter_modules(graph, modules_raw):
        if should_extract(name, encoding, loader):
            bundles.append((name, contents))

    if not bundles:
        raise ExtractionError("No JavaScript bundles found in module graph")

    output.mkdir(parents=True, exist_ok=True)

    written_names = set()

    for name, contents in bundles:
        rel_name = name.replace("/$bunfs/root/", "")
        base = rel_name.strip("/").replace("/", "_").replace(os.sep, "_") or "bundle"

        candidate = base
        counter = 1
        while candidate in written_names:
            candidate = f"{base}_{counter}"
            counter += 1

        written_names.add(candidate)
        dest = output / f"{candidate}.js"
        dest.write_bytes(contents)

        print(f"Saved {dest} ({len(contents)/1024:.1f} KB)")
        if prettify:
            try:
                run_prettier(dest)
                print("  ✓ prettified")
            except ExtractionError as exc:
                print(f"  ⚠ {exc}")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Extract Bun standalone JS bundles")
    parser.add_argument("binary", type=Path, help="Path to Bun/Claude executable")
    parser.add_argument(
        "output", type=Path, nargs="?", default=Path("extracted"), help="Destination directory"
    )
    parser.add_argument(
        "--no-prettier", dest="prettier", action="store_false", help="Skip Prettier formatting"
    )
    parser.add_argument(
        "--prettier-bin",
        default="prettier",
        help="Path to the Prettier CLI (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    if not args.binary.is_file():
        parser.error(f"Executable not found: {args.binary}")

    run_prettier.prettier_bin = args.prettier_bin

    try:
        extract(args.binary, args.output, prettify=args.prettier)
    except ExtractionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
