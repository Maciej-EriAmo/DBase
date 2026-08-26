"""P1–P5: KAFS journal, compact/seal, KAFD v2.1 footer, GOP, indeks tick/T."""
from __future__ import annotations

import os
import struct
import tempfile
import threading
import unittest
import zlib

from karmazyn_kafd import (
    F_FOOTER_TOC,
    KAFDAtom,
    KAFDFlowReader,
    KAFDFlowWriter,
    KAFDJournal,
    KAFDReader,
    KAFDWriter,
    VERSION_V21,
    compact_journal,
    peek_thermal_header,
)
from karmazyn_thermal import (
    KIND_DELTA,
    KIND_KEY,
    ThermalFrame,
    ThermalGopEncoder,
    ThermalLog,
)
from karmazyn_substrate import Store


def _frame(tick, temps, ids=None, states=None, causality=""):
    ids = ids or [f"a{i}" for i in range(len(temps))]
    states = states or (["HOT"] * len(temps))
    return ThermalFrame(
        timestamp=tick * 1000,
        tick_number=tick,
        num_atoms=len(temps),
        atom_ids=ids,
        temperatures=list(temps),
        states=list(states),
        causality_hash=causality,
    )


class TestKafsCrcRoundtrip(unittest.TestCase):
    def test_flow_writer_reader_crc(self):
        w = KAFDFlowWriter(meta={"lab": "t"})
        w.add(KAFDAtom("hot", b"AAA", mime="text/plain", T=80.0))
        w.add(KAFDAtom("warm", b"BBB", mime="text/plain", T=40.0))
        from io import BytesIO
        buf = BytesIO()
        n = w.write_stream(buf)
        self.assertGreater(n, 8)
        buf.seek(0)
        r = KAFDFlowReader(buf)
        r.read_header()
        atoms = list(r.iter_atoms())
        self.assertEqual([a.id for a in atoms], ["hot", "warm"])
        self.assertEqual(atoms[0].data, b"AAA")


class TestJournalP1(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.kafs")

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_checkpoint_recover(self):
        j = KAFDJournal(self.path, fsync=True)
        a = KAFDAtom("t1", _frame(1, [80.0]).to_bytes(), T=80.0)
        j.append_atom(a)
        j.checkpoint(tick=1, causality="abc", T_min=80.0, T_max=80.0, kind=KIND_KEY)
        self.assertEqual(j.n_frames, 1)
        j.close()

        j2 = KAFDJournal(self.path, fsync=False)
        atoms = list(j2.iter_atoms())
        self.assertEqual(len(atoms), 1)
        self.assertEqual(j2.n_frames, 1)
        self.assertTrue(j2.last_checkpoint)
        j2.close()

    def test_recover_truncated_tail(self):
        j = KAFDJournal(self.path, fsync=True)
        j.append_atom(KAFDAtom("t1", _frame(1, [80.0]).to_bytes(), T=80.0))
        j.append_atom(KAFDAtom("t2", _frame(2, [79.0]).to_bytes(), T=79.0))
        j.close()
        size = os.path.getsize(self.path)
        with open(self.path, "r+b") as f:
            f.truncate(size - 3)
        j2 = KAFDJournal(self.path, fsync=False)
        atoms = list(j2.iter_atoms())
        self.assertEqual(len(atoms), 1)
        self.assertEqual(atoms[0].id, "t1")
        j2.close()


class TestCompactP2P3(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jpath = os.path.join(self.tmp.name, "t.kafs")
        self.kpath = os.path.join(self.tmp.name, "t.kafd")

    def tearDown(self):
        self.tmp.cleanup()

    def test_compact_footer_tick_index_mmap(self):
        j = KAFDJournal(self.jpath, fsync=False)
        for t, temp in ((1, 80.0), (2, 70.0), (3, 40.0)):
            fr = _frame(t, [temp])
            j.append_atom(KAFDAtom(f"t{t}", fr.to_bytes(), T=temp, atype=4))
        j.close()
        info = compact_journal(self.jpath, self.kpath, layout="footer", reset_journal=True)
        self.assertEqual(info["atoms"], 3)
        self.assertTrue(os.path.exists(self.kpath))

        with KAFDReader.from_path(self.kpath) as r:
            self.assertTrue(r._flags & F_FOOTER_TOC)
            with open(self.kpath, "rb") as fh:
                head = fh.read(4)
            if head == b"KAFX":
                from karmazyn_cipher import read_stored_file
                blob, _mode = read_stored_file(self.kpath)
                ver = struct.unpack(">H", blob[4:6])[0]
            else:
                with open(self.kpath, "rb") as fh:
                    raw = fh.read(6)
                ver = struct.unpack(">H", raw[4:6])[0]
            self.assertEqual(ver, VERSION_V21)
            ids = r.atom_ids
            self.assertEqual(len(ids), 3)
            atom = r.get_atom("t1")
            self.assertIsNotNone(atom)
            idx = r.tick_index
            self.assertEqual(len(idx), 3)
            ticks = {rec["tick"] for rec in idx}
            self.assertEqual(ticks, {1, 2, 3})
            hits = r.ticks_in_T_range(35.0, 45.0)
            self.assertTrue(any(h["tick"] == 3 for h in hits))
            self.assertFalse(any(h["tick"] == 1 for h in hits))

        # v2.0 still works
        w = KAFDWriter(layout="table_first")
        w.add(KAFDAtom("x", b"hi", T=50.0))
        blob = w.build()
        r2 = KAFDReader(blob)
        self.assertEqual(r2.get_atom("x").data, b"hi")
        self.assertFalse(r2._flags & F_FOOTER_TOC)

    def test_cas_and_header_crc_reject(self):
        w = KAFDWriter(layout="footer")
        w.add(KAFDAtom("x", b"payload-bytes", T=50.0))
        blob = bytearray(w.build())
        blob[64] ^= 0xFF  # payload v2.1 zaczyna się po nagłówku
        with self.assertRaises(ValueError):
            KAFDReader(bytes(blob)).get_atom("x")

        blob2 = bytearray(w.build())
        blob2[10] ^= 0xFF  # w nagłówku
        with self.assertRaises(ValueError):
            KAFDReader(bytes(blob2))


class TestGopP4P5(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jpath = os.path.join(self.tmp.name, "th.kafs")
        self.kpath = os.path.join(self.tmp.name, "th.kafd")

    def tearDown(self):
        self.tmp.cleanup()

    def test_gop_smaller_delta(self):
        key = _frame(1, [50.0] * 24)
        nxt_temps = [50.0] * 24
        nxt_temps[0] = 49.0
        nxt = _frame(2, nxt_temps, causality=key.compute_hash())
        enc = ThermalGopEncoder(gop_size=8)
        k0, b0 = enc.encode(key)
        k1, b1 = enc.encode(nxt)
        self.assertEqual(k0, KIND_KEY)
        self.assertEqual(k1, KIND_DELTA)
        self.assertLess(len(b1), len(b0))
        back = ThermalFrame.decode(b1, key=key)
        self.assertAlmostEqual(back.temperatures[0], 49.0, places=4)

    def test_thermal_journal_as_of_and_chain(self):
        class MockStore:
            lock = threading.RLock()
            tick_count = 0
            def stats(self):
                return {}
            def atoms(self):
                return []

        tmem = ThermalLog(
            MockStore(),
            path=os.path.join(self.tmp.name, "th.kafd"),
            gop_size=2,
            compact_every=0,
            fsync=False,
        )
        self.addCleanup(tmem.close)
        prev = ""
        for i, temp in enumerate((80.0, 79.0, 78.0, 40.0), start=1):
            fr = _frame(i, [temp], causality=prev)
            self.assertTrue(tmem.commit_snapshot(fr))
            prev = fr.compute_hash()

        self.assertTrue(os.path.exists(tmem.journal_path))
        f2 = tmem.as_of_tick(2)
        self.assertIsNotNone(f2)
        self.assertEqual(f2.tick_number, 2)
        self.assertAlmostEqual(f2.temperatures[0], 79.0, places=4)

        f3 = tmem.as_of_tick(3)
        self.assertAlmostEqual(f3.temperatures[0], 78.0, places=4)

        ok, msg = tmem.verify_chain()
        self.assertTrue(ok, msg)

        hits = tmem.ticks_in_T_range(35.0, 45.0)
        self.assertTrue(any(h.get("tick") == 4 for h in hits))

        info = tmem.seal()
        self.assertTrue(info.get("ok"))
        self.assertTrue(os.path.exists(tmem.kafd_path))
        f2b = tmem.as_of_tick(2)
        self.assertIsNotNone(f2b)
        self.assertAlmostEqual(f2b.temperatures[0], 79.0, places=4)
        tmem.close()

    def test_store_tick_count_and_snapshot(self):
        store = Store(thermal=True)
        a = store.atom_new("doc", E="x", T=80.0)
        store.set_root(store.bubble_new("root"))
        # keep atom reachable
        b = store.roots[0]
        b.bind("x", a)
        tmem = ThermalLog(
            store,
            path=os.path.join(self.tmp.name, "s.kafd"),
            auto_snapshot=True,
            gop_size=4,
            fsync=False,
        )
        store.tick()
        self.assertEqual(store.tick_count, 1)
        self.assertGreaterEqual(tmem.frame_count, 1)
        fr = tmem.as_of_tick(1)
        self.assertIsNotNone(fr)
        self.assertEqual(fr.tick_number, 1)
        tmem.close()


class TestPeekHeader(unittest.TestCase):
    def test_peek_v2(self):
        fr = _frame(9, [80.0, 10.0])
        blob = fr.to_bytes()
        hdr = peek_thermal_header(blob)
        self.assertEqual(hdr["tick"], 9)
        self.assertEqual(hdr["kind"], KIND_KEY)
        self.assertGreaterEqual(hdr["T_max"], 79.0)
        self.assertLessEqual(hdr["T_min"], 11.0)


if __name__ == "__main__":
    unittest.main()
