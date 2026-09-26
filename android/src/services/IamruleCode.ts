// Read and patch the code section of a compiled App-Zone rule (IAMRULE ver 8) - used to add backlight
// flashes to native guided workouts, which the compiler never emits. Exact port of
// tools/iamrule_code.py (layout / instruction lengths / splice / add_lights); tested against the
// same live-compiler fixtures (__tests__/IamruleCode.test.ts).
//
// Layout after the header's dataOffset: u16 n_out, u16 n_builtin, u16 n_slots, n_slots x 12-byte
// slot records, u16 data-memory size, u16 pool size, pool, u16 code length, code (padded to an even
// length with one 0x00 after the final `end`). Jumps (jz/jmp/onerr) carry a u16 offset relative
// to the jump's own start, forward only. A native call is `21 <id u16> <0 u16> <argc u16>`:
// 0x1b = Suunto.alarmBeep(), 0x1c = Suunto.light(). A JSON-compiled guided workout is a fixed
// interpreter over a per-step table; native 0x27 shows each new step and the finish screen.

const OP_LEN: Record<number, number> = {};
for (const op of [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b, 0x0c,
  0x0e, 0x13, 0x18, 0x19, 0x1f]) OP_LEN[op] = 1;
OP_LEN[0x10] = 2;
for (const op of [0x0d, 0x12, 0x14, 0x15, 0x16, 0x1c, 0x50, 0x54, 0x55, 0x59]) OP_LEN[op] = 3;
OP_LEN[0x1b] = 5; OP_LEN[0x90] = 5;
OP_LEN[0x21] = 7;
const JUMPS = new Set([0x0d, 0x12, 0x16]);
const END = 0x1f;
const MAGIC = [0x49, 0x41, 0x4d, 0x52, 0x55, 0x4c, 0x45, 0x00]; // "IAMRULE\0"

export const CALL_BEEP = 0x1b;
export const CALL_LIGHT = 0x1c;
export const CALL_GUIDANCE_DISPLAY = 0x27;
export const CALL_LOAD_STEP = 0x29;

export interface Instruction { pc: number; op: number; raw: Uint8Array }

const u16 = (b: Uint8Array, o: number) => b[o] | (b[o + 1] << 8);
const u32 = (b: Uint8Array, o: number) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;
const f32 = (b: Uint8Array, o: number) => new DataView(b.buffer, b.byteOffset + o, 4).getFloat32(0, true);
const eq = (a: Uint8Array, b: Uint8Array) => a.length === b.length && a.every((x, i) => x === b[i]);

export function callBytes(callId: number, argc = 0): Uint8Array {
  return Uint8Array.from([0x21, callId & 0xff, callId >> 8, 0, 0, argc & 0xff, argc >> 8]);
}

export function layout(binary: Uint8Array) {
  if (!MAGIC.every((x, i) => binary[i] === x)) throw new Error('not an IAMRULE binary');
  const dataOffset = u32(binary, 24);
  const nSlots = u16(binary, dataOffset + 4);
  const recordsEnd = dataOffset + 6 + nSlots * 12;
  const poolSize = u16(binary, recordsEnd + 2);
  const codeLenPos = recordsEnd + 4 + poolSize;
  const codeLen = u16(binary, codeLenPos);
  const code = codeLenPos + 2;
  if (code + codeLen !== binary.length) throw new Error(`code section (${code}+${codeLen}) does not end at EOF (${binary.length})`);
  return { codeLenPos, code, codeLen };
}

export function instructions(binary: Uint8Array): Instruction[] {
  const code = binary.subarray(layout(binary).code);
  const out: Instruction[] = [];
  let pc = 0;
  while (pc < code.length) {
    const op = code[pc];
    const n = OP_LEN[op];
    if (n === undefined || pc + n > code.length) throw new Error(`unknown/truncated opcode 0x${op.toString(16)} at code+${pc}`);
    out.push({ pc, op, raw: code.subarray(pc, pc + n) });
    pc += n;
  }
  return out;
}

export const jumpTarget = (i: Instruction) => i.pc + u16(i.raw, 1);

/** Full decode, every jump lands on an instruction start, ends with `end`. */
export function validate(binary: Uint8Array): Instruction[] {
  const ins = instructions(binary);
  const starts = new Set(ins.map(i => i.pc));
  for (const i of ins) {
    if (JUMPS.has(i.op) && !starts.has(jumpTarget(i))) throw new Error(`jump at code+${i.pc} -> ${jumpTarget(i)} is not an instruction start`);
  }
  const tail = ins.filter(i => i.op !== 0x00);
  if (!tail.length || tail[tail.length - 1].op !== END) throw new Error('code does not end with `end`');
  return ins;
}

/** Replace `del` bytes at code offset `pos` (an instruction start) with `insert`, shifting every
 *  jump that crosses the edit and rewriting the code length + even-length pad. A jump targeting
 *  `pos` exactly keeps targeting it (lands on the inserted bytes). */
export function splice(binary: Uint8Array, pos: number, del = 0, insert: Uint8Array = new Uint8Array(0)): Uint8Array {
  const lay = layout(binary);
  const ins = validate(binary);
  const starts = new Set(ins.map(i => i.pc));
  let code = Array.from(binary.subarray(lay.code));
  if (code.length >= 2 && code[code.length - 1] === 0x00 && code[code.length - 2] === END) code.pop();
  if (!starts.has(pos) || pos >= code.length) throw new Error(`code+${pos} is not an instruction start`);
  const delta = insert.length - del;
  for (const i of ins) {
    if (!JUMPS.has(i.op)) continue;
    const tgt = jumpTarget(i);
    if (pos <= i.pc && i.pc < pos + del) {
      if (tgt <= pos + del) continue; // a jump that stays inside the removed block goes with it
      throw new Error(`jump at code+${i.pc} leaves the deleted range`);
    }
    if (pos < tgt && tgt < pos + del) throw new Error(`jump at code+${i.pc} lands inside the deleted range`);
    if (i.pc < pos && pos < tgt) {
      const off = tgt + delta - i.pc;
      if (off <= 0 || off > 0xffff) throw new Error('jump offset out of range');
      code[i.pc + 1] = off & 0xff; code[i.pc + 2] = off >> 8;
    }
  }
  code = [...code.slice(0, pos), ...Array.from(insert), ...code.slice(pos + del)];
  if (code.length % 2) code.push(0x00);
  const out = new Uint8Array(lay.codeLenPos + 2 + code.length);
  out.set(binary.subarray(0, lay.codeLenPos));
  out[lay.codeLenPos] = code.length & 0xff; out[lay.codeLenPos + 1] = code.length >> 8;
  out.set(code, lay.codeLenPos + 2);
  validate(out);
  return out;
}

export function callSites(binary: Uint8Array, callId: number): number[] {
  return instructions(binary).filter(i => i.op === 0x21 && u16(i.raw, 1) === callId).map(i => i.pc);
}

/** `if (slot == s1 || slot == s2 ...) Suunto.light();` in the compiler's own encoding; plain call
 *  when every step is chosen, empty when none is. */
export function lightIfStep(slot: number, steps: number[], total: number): Uint8Array {
  const s = Array.from(new Set(steps)).sort((a, b) => a - b);
  if (!s.length) return new Uint8Array(0);
  const light = callBytes(CALL_LIGHT);
  if (s.length === total && s.every((v, i) => v === i)) return light;
  const bytes: number[] = [];
  const f = new Uint8Array(4);
  s.forEach((v, k) => {
    new DataView(f.buffer).setFloat32(0, v, true);
    bytes.push(0x14, slot & 0xff, slot >> 8, 0x90, ...Array.from(f), 0x09);
    if (k) bytes.push(0x0c);
  });
  const jz = 3 + light.length;
  bytes.push(0x16, jz & 0xff, jz >> 8, ...Array.from(light));
  return Uint8Array.from(bytes);
}

export interface GuidanceSites {
  slot: number; total: number; stepStart: number; finish: number; limits: number | null;
  jumpTargets: Set<number>;
}

/** Where a JSON-compiled guided workout shows things: step counter slot + number of steps it runs
 *  (repeat-expanded), the display call for each new step, the finish display, the limits beep. */
export function guidanceSites(binary: Uint8Array): GuidanceSites {
  const ins = instructions(binary);
  const isCall = (i: Instruction, id: number) => i.op === 0x21 && u16(i.raw, 1) === id;
  const loads = ins.map((_, k) => k).filter(k => isCall(ins[k], CALL_LOAD_STEP) && k >= 2
    && ins[k - 2].op === 0x14 && ins[k - 1].op === 0x90);
  const displays = ins.map((_, k) => k).filter(k => isCall(ins[k], CALL_GUIDANCE_DISPLAY));
  const beeps = ins.map((_, k) => k).filter(k => isCall(ins[k], CALL_BEEP));
  if (loads.length !== 1 || displays.length !== 2 || beeps.length > 1) throw new Error('not the guided-workout template this code knows');
  const k = loads[0];
  const slot = u16(ins[k - 2].raw, 1);
  const total = f32(ins[k - 1].raw, 1);
  const advance = [Uint8Array.from([0x14, slot & 0xff, slot >> 8]), Uint8Array.from([0x90, 0, 0, 0x80, 0x3f]),
    Uint8Array.from([0x01]), Uint8Array.from([0x15, slot & 0xff, slot >> 8])];
  let found = false;
  for (let j = 0; j + 3 < ins.length && !found; j++) found = advance.every((a, n) => eq(ins[j + n].raw, a));
  if (!found) throw new Error('step counter slot not recognised');
  const stepDisplay = displays.find(d => d > k);
  const finishDisplay = displays.find(d => d !== stepDisplay);
  if (stepDisplay === undefined || finishDisplay === undefined || total !== Math.round(total)) throw new Error('guidance display calls not where expected');
  return {
    slot, total, stepStart: ins[stepDisplay + 1].pc, finish: ins[finishDisplay + 1].pc,
    limits: beeps.length ? ins[beeps[0]].pc : null,
    jumpTargets: new Set(ins.filter(i => JUMPS.has(i.op)).map(jumpTarget)),
  };
}

export interface LightChoices { onStepStart: number[]; onLimits: number[]; onFinish: boolean; expectedTotal?: number }

/** Guided workout + backlight: flash at the start of the chosen steps, together with the
 *  out-of-limits alarm on the chosen steps, and on the finish screen. Steps are 0-based positions in
 *  the repeat-expanded sequence. Returns [binary, flashesAdded]. */
export function addLights(binary: Uint8Array, c: LightChoices): [Uint8Array, number] {
  if (callSites(binary, CALL_LIGHT).length) return [binary, 0]; // already patched - idempotent
  const g = guidanceSites(binary);
  if (c.expectedTotal !== undefined && c.expectedTotal !== g.total) throw new Error(`workout has ${c.expectedTotal} steps, binary runs ${g.total}`);
  let edits: [number, Uint8Array][] = [[g.stepStart, lightIfStep(g.slot, c.onStepStart, g.total)]];
  if (c.onFinish) edits.push([g.finish, callBytes(CALL_LIGHT)]);
  if (c.onLimits.length) {
    if (g.limits === null) throw new Error('no out-of-limits alarm in this binary');
    edits.push([g.limits, lightIfStep(g.slot, c.onLimits, g.total)]);
  }
  edits = edits.filter(([, b]) => b.length);
  for (const [pos] of edits) if (g.jumpTargets.has(pos)) throw new Error(`insertion point code+${pos} is a jump target`);
  let out = binary;
  for (const [pos, block] of edits.sort((a, b) => b[0] - a[0])) out = splice(out, pos, 0, block);
  return [out, edits.length];
}
