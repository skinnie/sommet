#!/usr/bin/env python3
"""Read and patch the code section of a compiled App-Zone rule (IAMRULE ver 8) — used to add a
backlight flash to native guided workouts, which the compiler never emits.

Layout after the header's dataOffset: u16 n_out, u16 n_builtin, u16 n_slots, n_slots x 12-byte slot
records, u16 data-memory size, u16 pool size, pool, u16 code length, code. The code is padded to an
even length with one 0x00 after the final `end`.

Instructions are variable length (table below). Jumps (jz/jmp/onerr) carry a u16 offset relative to
the jump's own start, forward only. A native call is `21 <id u16> <0 u16> <argc u16>`:
0x1b = Suunto.alarmBeep(), 0x1c = Suunto.light(). A JSON-compiled guided workout is a fixed
interpreter over a per-step table; it calls native 0x27 to show each new step (and the finish
screen) on the guidance display — the natural place for a light flash.

`splice()` was checked byte-exact against the live compiler's own output (inserting/removing
alarmBeep/light calls, even and odd lengths, both directions) — see test_iamrule_code.py.

    ./tools/iamrule_code.py compiled.bin            # disassemble (.bin, or .json with "binary")
"""
import json
import struct
import sys

OP_LEN = {
    **dict.fromkeys((0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b, 0x0c,
                     0x0e, 0x13, 0x18, 0x19, 0x1f), 1),
    0x10: 2,
    **dict.fromkeys((0x0d, 0x12, 0x14, 0x15, 0x16, 0x1c, 0x50, 0x54, 0x55, 0x59), 3),
    0x1b: 5, 0x90: 5,
    0x21: 7,
}
OP_NAME = {0x00: "pad", 0x01: "add", 0x02: "sub", 0x03: "mul", 0x04: "div", 0x05: "lt",
           0x06: "le", 0x07: "gt", 0x08: "ge", 0x09: "eq", 0x0a: "ne", 0x0b: "and", 0x0c: "or",
           0x0e: "not", 0x13: "errh", 0x18: "strend", 0x19: "strbeg", 0x1f: "end",
           0x10: "pushb", 0x0d: "jmp", 0x12: "onerr", 0x14: "load", 0x15: "store", 0x16: "jz",
           0x1c: "isinv", 0x50: "pushw", 0x54: "aload", 0x55: "astore", 0x59: "setstr",
           0x1b: "arrcpy", 0x90: "pushf", 0x21: "call"}
JUMPS = (0x0d, 0x12, 0x16)
END = 0x1f

CALL_BEEP = 0x1b
CALL_LIGHT = 0x1c
CALL_GUIDANCE_DISPLAY = 0x27


def call_bytes(call_id, argc=0):
    return struct.pack("<BHHH", 0x21, call_id, 0, argc)


def layout(binary):
    if binary[:8] != b"IAMRULE\0":
        raise ValueError("not an IAMRULE binary")
    data_offset = struct.unpack_from("<I", binary, 24)[0]
    n_out, n_builtin, n_slots = struct.unpack_from("<HHH", binary, data_offset)
    records_end = data_offset + 6 + n_slots * 12
    _mem_size, pool_size = struct.unpack_from("<HH", binary, records_end)
    code_len_pos = records_end + 4 + pool_size
    code_len = struct.unpack_from("<H", binary, code_len_pos)[0]
    code = code_len_pos + 2
    if code + code_len != len(binary):
        raise ValueError(f"code section ({code}+{code_len}) does not end at EOF ({len(binary)})")
    return {"code_len_pos": code_len_pos, "code": code, "code_len": code_len}


def instructions(binary):
    """[(offset_in_code, opcode, raw_bytes)] for the whole code section; raises on an unknown
    opcode or if decoding doesn't end exactly at the end of the code."""
    lay = layout(binary)
    code = binary[lay["code"]:]
    out, pc = [], 0
    while pc < len(code):
        op = code[pc]
        n = OP_LEN.get(op)
        if n is None or pc + n > len(code):
            raise ValueError(f"unknown/truncated opcode 0x{op:02x} at code+{pc}")
        out.append((pc, op, code[pc:pc + n]))
        pc += n
    return out


def jump_target(ins):
    pc, _op, raw = ins
    return pc + struct.unpack_from("<H", raw, 1)[0]


def validate(binary):
    """Structural check: full decode, every jump lands on an instruction start, ends with `end`."""
    ins = instructions(binary)
    starts = {pc for pc, _, _ in ins}
    for i in ins:
        if i[1] in JUMPS and jump_target(i) not in starts:
            raise ValueError(f"jump at code+{i[0]} -> {jump_target(i)} is not an instruction start")
    tail = [i for i in ins if i[1] != 0x00]
    if not tail or tail[-1][1] != END:
        raise ValueError("code does not end with `end`")
    return ins


def splice(binary, pos, delete=0, insert=b""):
    """Replace `delete` bytes at code offset `pos` (an instruction start) with `insert`, shifting
    every jump that crosses the edit, rewriting the code length and the even-length pad. A jump
    that targets `pos` exactly keeps targeting it, i.e. it now lands on the inserted bytes."""
    lay = layout(binary)
    ins = validate(binary)
    starts = {pc for pc, _, _ in ins}
    code = bytearray(binary[lay["code"]:])
    if code[-1] == 0x00 and len(code) >= 2 and code[-2] == END:
        del code[-1]
    if pos not in starts or pos >= len(code):
        raise ValueError(f"code+{pos} is not an instruction start")
    delta = len(insert) - delete
    for i in ins:
        pc, op, _ = i
        if op not in JUMPS:
            continue
        tgt = jump_target(i)
        if pos <= pc < pos + delete or pos < tgt < pos + delete:
            raise ValueError(f"jump at code+{pc} touches the deleted range")
        if pc < pos < tgt or (delete and pc < pos and tgt == pos + delete):
            new_off = tgt + delta - pc
            if not 0 < new_off <= 0xFFFF:
                raise ValueError("jump offset out of range")
            struct.pack_into("<H", code, pc + 1, new_off)
    code[pos:pos + delete] = insert
    if len(code) % 2:
        code.append(0x00)
    out = binary[:lay["code_len_pos"]] + struct.pack("<H", len(code)) + bytes(code)
    validate(out)
    return out


def call_sites(binary, call_id):
    return [pc for pc, op, raw in instructions(binary)
            if op == 0x21 and struct.unpack_from("<H", raw, 1)[0] == call_id]


def add_light_on_step_change(binary):
    """Insert Suunto.light() right after every guidance-display update (each new step + the
    finish screen) of a JSON-compiled guided workout. Returns (patched_binary, sites_patched).
    Raises ValueError if the binary isn't the expected guidance template."""
    sites = call_sites(binary, CALL_GUIDANCE_DISPLAY)
    if not sites:
        raise ValueError("no guidance-display call (0x27) found - not a guided-workout binary")
    if call_sites(binary, CALL_LIGHT):
        return binary, 0   # already has a light call - leave it alone (idempotent)
    ins = instructions(binary)
    after = {pc: ins[k + 1][0] for k, (pc, _, _) in enumerate(ins[:-1])}
    targets = {jump_target(i) for i in ins if i[1] in JUMPS}
    if any(after[pc] in targets for pc in sites):
        # Another path jumps to the statement after the display call; inserting there would flash
        # the light on that path too. Not seen in any template so far - refuse rather than guess.
        raise ValueError("instruction after a guidance-display call is a jump target")
    out = binary
    for pc in sorted(sites, reverse=True):   # back to front: earlier offsets stay valid
        out = splice(out, after[pc], insert=call_bytes(CALL_LIGHT))
    return out, len(sites)


def disassemble(binary):
    lines = []
    for pc, op, raw in instructions(binary):
        name, arg = OP_NAME.get(op, f"op{op:02x}"), ""
        if op == 0x90:
            arg = f"{struct.unpack_from('<f', raw, 1)[0]:g}"
        elif op == 0x21:
            cid, _b, argc = struct.unpack_from("<HHH", raw, 1)
            arg = {CALL_BEEP: "alarmBeep", CALL_LIGHT: "light"}.get(cid, f"native 0x{cid:02x}")
            arg += f" argc={argc}"
        elif op == 0x1b:
            arg = "%d <- %d" % struct.unpack_from("<HH", raw, 1)
        elif op == 0x10:
            arg = str(raw[1])
        elif len(raw) == 3:
            v = struct.unpack_from("<H", raw, 1)[0]
            arg = f"-> {pc + v}" if op in JUMPS else str(v)
        lines.append(f"{pc:5d}  {raw.hex(' '):22s} {name:7s} {arg}")
    return "\n".join(lines)


def _load(path):
    if path.endswith(".json"):
        return bytes(json.load(open(path))["binary"])
    return open(path, "rb").read()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(disassemble(_load(sys.argv[1])))
