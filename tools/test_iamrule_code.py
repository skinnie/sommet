#!/usr/bin/env python3
"""Offline tests for iamrule_code.py against real compiler outputs (testdata/iamrule_oracle.json).
No watch, no network.

    ./tools/test_iamrule_code.py            # or: python3 -m unittest test_iamrule_code
"""
import json
import pathlib
import unittest

import iamrule_code as IC

FX = {k: bytes.fromhex(v) for k, v in
      json.loads((pathlib.Path(__file__).parent / "testdata" / "iamrule_oracle.json").read_text()).items()
      if not k.startswith("_")}
BEEP, LIGHT = IC.call_bytes(IC.CALL_BEEP), IC.call_bytes(IC.CALL_LIGHT)
GUIDANCE = ("guidance_2step", "guidance_3step", "guidance_2step_hr")


class Decode(unittest.TestCase):
    def test_every_fixture_decodes_and_validates(self):
        for name, binary in FX.items():
            with self.subTest(name):
                IC.validate(binary)
                self.assertTrue(IC.disassemble(binary))

    def test_guidance_template_is_step_count_independent(self):
        # a 3-step workout differs from a 2-step one only in its data table + a few count constants:
        # same code length, same instruction layout
        two, three = (IC.instructions(FX[k]) for k in ("guidance_2step", "guidance_3step"))
        self.assertEqual([(pc, op) for pc, op, _ in two], [(pc, op) for pc, op, _ in three])


class SpliceMatchesCompiler(unittest.TestCase):
    """The live compiler's own outputs for sources that differ only by the calls are the oracle."""

    def _site(self, binary, call_id):
        return IC.call_sites(binary, call_id)[0]

    def test_even_insert(self):
        pos = self._site(FX["hand_beep_light_even"], IC.CALL_BEEP)
        self.assertEqual(IC.splice(FX["hand_no_calls_even"], pos, insert=BEEP + LIGHT),
                         FX["hand_beep_light_even"])

    def test_even_delete(self):
        pos = self._site(FX["hand_beep_light_even"], IC.CALL_BEEP)
        self.assertEqual(IC.splice(FX["hand_beep_light_even"], pos, delete=14),
                         FX["hand_no_calls_even"])

    def test_odd_insert_repads(self):
        pos = self._site(FX["hand_light_odd"], IC.CALL_LIGHT)
        self.assertEqual(IC.splice(FX["hand_no_calls_odd"], pos, insert=LIGHT), FX["hand_light_odd"])

    def test_odd_delete_repads(self):
        pos = self._site(FX["hand_light_odd"], IC.CALL_LIGHT)
        self.assertEqual(IC.splice(FX["hand_light_odd"], pos, delete=7), FX["hand_no_calls_odd"])


class LightOnStepChange(unittest.TestCase):
    def test_light_follows_every_guidance_display_update(self):
        for name in GUIDANCE:
            with self.subTest(name):
                patched, n = IC.add_light_on_step_change(FX[name])
                self.assertEqual(n, 2)   # each new step + the finish screen share 2 display sites
                ins = IC.instructions(patched)
                for k, (pc, op, raw) in enumerate(ins):
                    if op == 0x21 and raw[1] == IC.CALL_GUIDANCE_DISPLAY:
                        self.assertEqual(ins[k + 1][2], LIGHT)
                self.assertEqual(len(IC.call_sites(patched, IC.CALL_LIGHT)), 2)

    def test_removing_the_lights_gives_back_the_compiler_bytes(self):
        for name in GUIDANCE:
            with self.subTest(name):
                patched, _ = IC.add_light_on_step_change(FX[name])
                for pc in sorted(IC.call_sites(patched, IC.CALL_LIGHT), reverse=True):
                    patched = IC.splice(patched, pc, delete=len(LIGHT))
                self.assertEqual(patched, FX[name])

    def test_idempotent(self):
        once, _ = IC.add_light_on_step_change(FX["guidance_2step_hr"])
        twice, n = IC.add_light_on_step_change(once)
        self.assertEqual((twice, n), (once, 0))

    def test_refuses_a_non_guidance_binary(self):
        with self.assertRaises(ValueError):
            IC.add_light_on_step_change(FX["hand_no_calls_even"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
