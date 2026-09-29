"""Testy oficjalnego klienta cynober_client.py (v7.7)."""

import threading
import time
import unittest

from cynober_client import CynoberClient, CynoberClientError, connect
from tests.test_server_rpc import TestServerHarness


class TestCynoberClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harness = TestServerHarness()
        cls.port = cls.harness.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()

    def test_connect_and_health(self):
        with CynoberClient(port=self.port) as c:
            row = c.query_line("ZDROWIE")
            self.assertEqual(row["action"], "HEALTH")
            from cynober_ops import SERVER_VERSION

            self.assertEqual(row["data"]["server_version"], SERVER_VERSION)
            self.assertEqual(row["data"].get("l0_carrier"), "tcp")
            self.assertTrue(row["data"].get("kpc"))

    def test_context_manager(self):
        c = CynoberClient(port=self.port)
        with c:
            self.assertIsNotNone(c.crypto_mode)
        self.assertIsNone(c.sock)

    def test_connect_helper(self):
        c = connect(host="127.0.0.1", port=self.port)
        try:
            m = c.query_line("METRYKI SERWERA")
            self.assertEqual(m["action"], "SERVER_METRICS")
        finally:
            c.close()

    def test_persistent_session_many_queries(self):
        """Stałe połączenie: wiele query na jednym TCP (bez reconnect)."""
        c = CynoberClient(port=self.port)
        c.connect()
        try:
            sock0 = c.sock
            fd0 = sock0.fileno()
            for _ in range(5):
                row = c.query_line("ZDROWIE")
                self.assertEqual(row["action"], "HEALTH")
                self.assertIs(c.sock, sock0)
                self.assertEqual(c.sock.fileno(), fd0)
            self.assertTrue(c.session_info()["connected"])
        finally:
            c.close()
            self.assertIsNone(c.sock)

    def test_query_reconnects_after_dead_sock(self):
        """ensure_connected + query: martwy lokalny sock → nowy handshake."""
        c = CynoberClient(port=self.port)
        c.connect()
        try:
            row = c.query_line("ZDROWIE")
            self.assertEqual(row["action"], "HEALTH")
            dead = c.sock
            dead.close()
            self.assertFalse(c._sock_alive())
            row2 = c.query_line("ZDROWIE")
            self.assertEqual(row2["action"], "HEALTH")
            self.assertTrue(c._sock_alive())
            self.assertIsNot(c.sock, dead)
        finally:
            c.close()

    def test_broken_kafs_get_closes_tunnel(self):
        """Urwany strumień KAFS zamyka gniazdo — kolejne get nie czyta cudzych bajtów."""
        import cynober_client as cc

        c = CynoberClient(port=self.port, timeout=8)
        c.connect()
        self.addCleanup(c.close)
        blob_a = b"AAAA" * 80
        blob_b = b"BBBB" * 80
        blob_c = b"CCCC" * 80
        self.assertEqual(c.put_media("a1", blob_a).get("status"), "ok")
        self.assertEqual(c.put_media("b1", blob_b).get("status"), "ok")
        self.assertEqual(c.put_media("c1", blob_c).get("status"), "ok")

        orig = cc._recv_frame
        state = {"n": 0, "arm": False}

        def wrapped(sock, deadline=0.0):
            data = orig(sock, deadline)
            if state["arm"] and sock is c.sock:
                state["n"] += 1
                if state["n"] == 2:
                    raise CynoberClientError("injected mid-get")
            return data

        cc._recv_frame = wrapped
        try:
            state["arm"] = True
            with self.assertRaises(CynoberClientError):
                c.get_media("a1")
            self.assertIsNone(c.sock)
        finally:
            state["arm"] = False
            cc._recv_frame = orig

        with self.assertRaises(CynoberClientError) as caught:
            c.get_media("c1")
        self.assertNotIn("BBBB", str(caught.exception))

    def test_timeout_after_send_does_not_rerun(self):
        """Timeout po wysłaniu ramki nie wykonuje polecenia drugi raz."""
        import cynober_server as srv

        orig = srv.CynoberFacade._execute_unlocked
        counts = {"n": 0}

        def wrapped(self, query):
            if "SLOWMARK" in query:
                counts["n"] += 1
                time.sleep(1.0)
                return [{"status": "ok", "action": "SLOW", "n": counts["n"]}]
            return orig(self, query)

        srv.CynoberFacade._execute_unlocked = wrapped
        c = CynoberClient(port=self.port, timeout=0.3)
        try:
            c.connect()
            with self.assertRaises(CynoberClientError):
                c.query("SLOWMARK")
            self.assertEqual(counts["n"], 1)
            self.assertIsNone(c.sock)
        finally:
            srv.CynoberFacade._execute_unlocked = orig
            c.close()

    def test_peer_tunnel_one_query_at_a_time(self):
        """Dwa wątki na cache'owanym tunelu nie czytają sobie odpowiedzi."""
        from cynober_replicate import _PeerRpc, reset_peer_sessions

        reset_peer_sessions()
        self.addCleanup(reset_peer_sessions)
        client = _PeerRpc("127.0.0.1", self.port, timeout=8)
        client.connect()
        self.addCleanup(client.close)
        swapped: list[tuple[str, str]] = []
        errors: list[str] = []
        lock = threading.Lock()

        def worker(text: str, expect: str) -> None:
            for _ in range(12):
                try:
                    row = client.query(text)
                    got = (row.get("results") or [{}])[0].get("action")
                    if got != expect:
                        with lock:
                            swapped.append((expect, str(got)))
                except Exception as e:
                    with lock:
                        errors.append(f"{type(e).__name__}: {e}")

        threads = [
            threading.Thread(target=worker, args=("ZDROWIE", "HEALTH")),
            threading.Thread(target=worker, args=("STATYSTYKI", "STATS")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(swapped, [])
        self.assertEqual(errors, [])

    def test_context_keeps_open_session(self):
        """with na żywej sesji nie robi drugiego handshake."""
        c = CynoberClient(port=self.port)
        c.connect()
        try:
            sock = c.sock
            with c:
                self.assertIs(c.sock, sock)
                row = c.query_line("ZDROWIE")
                self.assertEqual(row["action"], "HEALTH")
            self.assertIsNone(c.sock)
            info = c.session_info()
            self.assertFalse(info["connected"])
            self.assertIsNone(info["crypto_mode"])
            self.assertFalse(info["kafs_enabled"])
        finally:
            c.close()

    def test_peer_connect_is_single_flight(self):
        """Kilka wątków na pusty cache dostaje ten sam tunel."""
        from cynober_replicate import _peer_client, reset_peer_sessions

        reset_peer_sessions()
        self.addCleanup(reset_peer_sessions)
        peer = {"host": "127.0.0.1", "port": self.port, "name": "local"}
        barrier = threading.Barrier(4)
        found: list = []
        errors: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                barrier.wait(5)
                client = _peer_client(peer)
                with lock:
                    found.append(client)
            except Exception as e:
                with lock:
                    errors.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual(errors, [])
        self.assertEqual(len(found), 4)
        self.assertTrue(all(client is found[0] for client in found))
        row = found[0].query("ZDROWIE")
        self.assertEqual(row["results"][0]["action"], "HEALTH")

    def test_get_media_resilient_reopens_world(self):
        """Zerwany tunel wstaje, wraca do świata i oddaje właściwe bajty."""
        name = f"mfr{time.time_ns()}"
        blob = b"BBBB" * 40
        c = CynoberClient(port=self.port, timeout=8)
        c.connect()
        self.addCleanup(c.close)
        try:
            created = c.query_line(f'UTWÓRZ ŚWIAT "{name}"')
            self.assertEqual(created.get("status"), "ok", created)
            chosen = c.query_line(f'WYBIERZ ŚWIAT "{name}"')
            self.assertEqual(chosen.get("status"), "ok", chosen)
            self.assertEqual(
                c.put_media("b1", blob, bubble="Album", binding="klip").get("status"),
                "ok",
            )
            saved = c.query_line("ZAPISZ ŚWIAT")
            self.assertEqual(saved.get("status"), "ok", saved)
            stat = c.media_stat("b1")
            self.assertEqual(stat.get("status"), "ok", stat)

            calls = {"n": 0}
            real_get = c.get_media

            def flaky(atom_id: str, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    c.close()
                    raise CynoberClientError("injected drop")
                return real_get(atom_id, **kwargs)

            c.get_media = flaky  # type: ignore[method-assign]
            data, _mime, meta = c.get_media_resilient("b1", world=name)
            self.assertEqual(data, blob)
            self.assertEqual(meta.get("id"), "b1")
            self.assertEqual(calls["n"], 2)
        finally:
            if not c._sock_alive():
                c.connect()
            c.query("ODŁĄCZ ŚWIAT")
            c.query(f'USUŃ ŚWIAT "{name}"')