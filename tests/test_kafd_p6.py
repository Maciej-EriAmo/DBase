"""P6: KAFX AES-256-GCM, klucz per świat, odczyt legacy XOR."""
from __future__ import annotations

import os
import tempfile
import unittest

os.environ["CYNOBER_MASTER_KEY"] = "ab" * 32

from karmazyn_cipher import (
    KAFX_MAGIC,
    crypto_available,
    derive_world_key,
    is_kafx,
    legacy_phi_xor,
    open_stored_bytes,
    unwrap_kafx,
    wrap_kafx,
)
from karmazyn_kafd import F_ENCRYPTED, KAFDAtom, KAFDJournal, KAFDReader, compact_journal
from karmazyn_store import load_documents, save_documents
from karmazyn_substrate import Store


@unittest.skipUnless(crypto_available(), "brak cryptography (AES-GCM)")
class TestKafxEnvelope(unittest.TestCase):
    def test_wrap_unwrap_roundtrip(self):
        pt = b"KAFD" + b"\x00" * 60 + b"hello-world"
        ct = wrap_kafx(pt, "Alpha")
        self.assertTrue(ct.startswith(KAFX_MAGIC))
        self.assertNotEqual(ct, pt)
        self.assertEqual(unwrap_kafx(ct), pt)

    def test_worlds_isolated(self):
        pt = b"KAFD" + b"secret-payload"
        a = wrap_kafx(pt, "A")
        b = wrap_kafx(pt, "B")
        self.assertNotEqual(a, b)
        self.assertEqual(unwrap_kafx(a), pt)
        with self.assertRaises(ValueError):
            unwrap_kafx(a, world="B")
        k_a = derive_world_key("A")
        k_b = derive_world_key("B")
        self.assertNotEqual(k_a, k_b)

    def test_wrong_master_fails(self):
        pt = b"KAFDxxxx"
        ct = wrap_kafx(pt, "W", master=b"\x11" * 32)
        with self.assertRaises(ValueError):
            unwrap_kafx(ct, master=b"\x22" * 32)

    def test_legacy_xor_still_opens(self):
        inner = b"KAFD" + b"\x00" * 20
        blob = legacy_phi_xor(inner)
        self.assertFalse(is_kafx(blob))
        plain, mode = open_stored_bytes(blob)
        self.assertEqual(mode, "xor-legacy")
        self.assertEqual(plain[:4], b"KAFD")


@unittest.skipUnless(crypto_available(), "brak cryptography (AES-GCM)")
class TestStoreKafx(unittest.TestCase):
    def test_save_load_encrypted(self):
        store = Store(thermal=False)
        a = store.atom_new("document", E="tajne", value="tajne")
        a.metadata["data"] = b"payload-secret"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Fort.kafd")
            n = save_documents(store, path, world="Fort")
            self.assertGreaterEqual(n, 1)
            with open(path, "rb") as f:
                head = f.read(4)
            self.assertEqual(head, KAFX_MAGIC)
            store2 = Store(thermal=False)
            n2 = load_documents(store2, path, world="Fort")
            self.assertGreaterEqual(n2, 1)
            atom = store2.get_atom(a.id)
            self.assertIsNotNone(atom)
            self.assertEqual(atom.metadata.get("data"), b"payload-secret")
            reader = KAFDReader.from_path(path, world="Fort")
            self.assertTrue(reader._flags & F_ENCRYPTED)


@unittest.skipUnless(crypto_available(), "brak cryptography (AES-GCM)")
class TestJournalKafx(unittest.TestCase):
    def test_journal_payload_and_seal(self):
        from karmazyn_thermal import ThermalFrame

        fr = ThermalFrame(
            timestamp=1, tick_number=7, num_atoms=1,
            atom_ids=["a0"], temperatures=[80.0], states=["HOT"],
        )
        blob = fr.to_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            jpath = os.path.join(tmp, "Fort.kafs")
            kpath = os.path.join(tmp, "Fort.kafd")
            j = KAFDJournal(jpath, fsync=False, world="Fort", encrypt=True)
            j.append_atom(KAFDAtom("t7", blob, T=80.0, atype=4))
            on_disk = list(j.iter_atoms())
            self.assertEqual(on_disk[0].data, blob)
            j.close()
            # surowa ramka na dysku jest KX1, nie ThermalFrame
            with open(jpath, "rb") as fh:
                raw = fh.read()
            self.assertIn(b"KX1\0", raw)
            self.assertNotIn(blob[:8], raw)

            info = compact_journal(
                jpath, kpath, layout="footer", world="Fort", encrypt=True
            )
            self.assertEqual(info["atoms"], 1)
            with open(kpath, "rb") as f:
                self.assertEqual(f.read(4), KAFX_MAGIC)
            r = KAFDReader.from_path(kpath, world="Fort")
            atom = r.get_atom("t7")
            self.assertEqual(atom.data, blob)
            self.assertTrue(r._flags & F_ENCRYPTED)
            r.close()


if __name__ == "__main__":
    unittest.main()
