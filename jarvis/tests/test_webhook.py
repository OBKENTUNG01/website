"""Uji webhook Telegram yang tidak menyerah (bot tidak boleh tuli).

Kejadian nyata 29 Okt 2026: setWebhook gagal "Failed to resolve host" pada 3 dari 4
restart (hostname tunnel baru belum dikenal resolver Telegram), bot menyerah, dan
karena mode webhook tidak pernah memanggil getUpdates, bot di komputer itu tuli."""

import asyncio
import logging
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import httpx

from itsme import config, main, telegram
from test_main import FakeTelegram

URL = "https://contoh-baru.trycloudflare.com"
SECRET = "s3cret"


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payload=None, exc=None):
        self.payload, self.exc, self.calls = payload, exc, []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        if self.exc:
            raise self.exc
        return FakeResponse(self.payload)

    async def post(self, url, json=None):
        self.calls.append((url, json, None))
        if self.exc:
            raise self.exc
        return FakeResponse(self.payload)

    async def aclose(self):
        pass


class TelegramClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tg = telegram.Telegram("TOKEN", "https://contoh.invalid")
        await self.tg.close()

    async def test_host_resolves_true_when_public_dns_knows_it(self):
        self.tg._client = FakeClient({"Status": 0, "Answer": [{"data": "104.16.1.1"}]})
        self.assertTrue(await self.tg.host_resolves("x.trycloudflare.com"))
        url, params, headers = self.tg._client.calls[0]
        self.assertEqual(params, {"name": "x.trycloudflare.com", "type": "A"})
        self.assertEqual(headers["accept"], "application/dns-json")

    async def test_host_resolves_false_for_nxdomain_empty_answer_or_network_error(self):
        for payload in ({"Status": 3}, {"Status": 0, "Answer": []}, {}):
            self.tg._client = FakeClient(payload)
            self.assertFalse(await self.tg.host_resolves("x.trycloudflare.com"), payload)
        self.tg._client = FakeClient(exc=httpx.ConnectError("putus"))
        self.assertFalse(await self.tg.host_resolves("x.trycloudflare.com"))

    async def test_resolve_ipv4_returns_only_a_records(self):
        self.tg._client = FakeClient({"Status": 0, "Answer": [
            {"name": "x.trycloudflare.com", "type": 5, "data": "y.example."},  # CNAME diabaikan
            {"type": 1, "data": "104.16.231.132"},
            {"type": 1, "data": "104.16.230.132"},
            {"type": 1, "data": "104.16.231.132"},  # duplikat
            {"type": 28, "data": "2606:4700::6810:e784"},  # AAAA diabaikan
            {"type": 1, "data": "bukan-ip"},
        ]})
        self.assertEqual(await self.tg.resolve_ipv4("x.trycloudflare.com"), ["104.16.231.132", "104.16.230.132"])

    async def test_resolve_ipv4_is_empty_when_unknown_or_unreachable(self):
        for payload in ({"Status": 3}, {"Status": 0}, {"Answer": None}, ["bukan", "dict"]):
            self.tg._client = FakeClient(payload)
            self.assertEqual(await self.tg.resolve_ipv4("x.trycloudflare.com"), [], payload)
        self.tg._client = FakeClient(exc=httpx.ConnectError("putus"))
        self.assertEqual(await self.tg.resolve_ipv4("x.trycloudflare.com"), [])

    async def test_set_webhook_sends_the_fixed_ip_only_when_given(self):
        self.tg._client = FakeClient({"ok": True})
        self.assertTrue(await self.tg.set_webhook(URL, SECRET, ip_address="104.16.231.132"))
        sent = self.tg._client.calls[0][1]
        self.assertEqual(sent["ip_address"], "104.16.231.132")
        self.assertEqual(sent["url"], f"{URL}/tg/{SECRET}")
        self.assertEqual(sent["secret_token"], SECRET)
        self.tg._client = FakeClient({"ok": True})
        self.assertTrue(await self.tg.set_webhook(URL, SECRET))
        self.assertNotIn("ip_address", self.tg._client.calls[0][1])

    async def _poll_once(self, description):
        self.tg._client = FakeClient({"ok": False, "description": description})
        with mock.patch.object(telegram.asyncio, "sleep", mock.AsyncMock()):
            return await self.tg.get_updates()

    async def test_conflict_caused_by_a_webhook_is_not_blamed_on_another_device(self):
        for desc in ("Conflict: terminated by setWebhook request",
                     "Conflict: can't use getUpdates method while webhook is active; use deleteWebhook to delete the webhook first"):
            self.tg._conflict_logged_at = None
            with self.assertLogs("itsme", level="INFO") as cm:
                self.assertEqual(await self._poll_once(desc), [])
            self.assertEqual([r.levelname for r in cm.records], ["INFO"], desc)
            self.assertNotIn("LAIN", cm.output[0])

    async def test_conflict_from_another_poller_still_warns(self):
        self.tg._conflict_logged_at = None
        with self.assertLogs("itsme", level="INFO") as cm:
            await self._poll_once("Conflict: terminated by other getUpdates request; make sure that only one bot instance is running")
        self.assertEqual([r.levelname for r in cm.records], ["WARNING"])
        self.assertIn("LAIN", cm.output[0])

    async def test_get_webhook_info(self):
        info = {"url": URL + "/tg/abc", "pending_update_count": 2}
        self.tg._client = FakeClient({"ok": True, "result": info})
        self.assertEqual(await self.tg.get_webhook_info(), info)
        self.tg._client = FakeClient({"ok": False, "description": "x"})
        self.assertIsNone(await self.tg.get_webhook_info())
        self.tg._client = FakeClient(exc=httpx.ConnectError("putus"))
        self.assertIsNone(await self.tg.get_webhook_info())


class FakeTg:
    """Telegram tiruan yang jawabannya bisa diatur; yang terakhir diulang terus."""

    def __init__(self, dns=(True,), sets=(True,), infos=(None,), ips=()):
        self.dns, self.sets, self.infos, self.ips = list(dns), list(sets), list(infos), list(ips)
        self.calls = []

    @staticmethod
    def _next(q):
        return q.pop(0) if len(q) > 1 else q[0]

    async def host_resolves(self, host):
        self.calls.append(("dns", host))
        return self._next(self.dns)

    async def resolve_ipv4(self, host):
        self.calls.append(("ipv4", host))
        return list(self.ips)

    async def set_webhook(self, url, secret, jeda=None, ip_address=None):
        self.calls.append(("set", url, ip_address))
        return self._next(self.sets)

    async def get_webhook_info(self):
        self.calls.append(("info",))
        return self._next(self.infos)

    async def get_updates(self):
        self.calls.append(("poll",))
        await asyncio.sleep(0)
        return []


class KeepWebhookTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for p in (mock.patch.object(config, "OWNER_ID", 42),
                  mock.patch.object(config, "MEMORY_FILE", os.path.join(self.dir, "memory.md")),
                  mock.patch.object(config, "DATA_DIR", self.dir),
                  mock.patch.object(config, "LOG_FILE", os.path.join(self.dir, "itsme.log")),
                  mock.patch.object(config, "WHATSAPP", False),
                  mock.patch.object(config, "TG_WEBHOOK", True),
                  mock.patch.object(main, "Telegram", FakeTelegram),
                  mock.patch.object(main, "tg_webhook_secret", return_value=SECRET)):
            p.start()
            self.addCleanup(p.stop)
        real_sleep = self.real_sleep = asyncio.sleep

        async def fast_sleep(delay, *a, **k):  # waktu tunggu nyata tidak perlu di uji
            await real_sleep(0)

        p = mock.patch.object(main.asyncio, "sleep", fast_sleep)
        p.start()
        self.addCleanup(p.stop)
        logging.getLogger("itsme").disabled = True
        self.addCleanup(setattr, logging.getLogger("itsme"), "disabled", False)
        self.app = main.App()
        self.app.loop = asyncio.get_running_loop()

    async def _until(self, cond, limit=2.0):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            if cond():
                return True
            await asyncio.sleep(0)
        return False

    async def _run_keeper(self, tg):
        self.app.tg = tg
        task = asyncio.ensure_future(self.app._keep_webhook(URL))
        self.addCleanup(task.cancel)
        return task

    async def test_waits_for_public_dns_before_calling_setwebhook(self):
        tg = FakeTg(dns=[False, False, True])
        await self._run_keeper(tg)
        self.assertTrue(await self._until(lambda: self.app._webhook_ok))
        kinds = [c[0] for c in tg.calls if c[0] != "ipv4"]
        self.assertEqual(kinds[:4], ["dns", "dns", "dns", "set"])  # baru memasang setelah DNS dikenal

    async def test_installs_with_the_fixed_ip_from_public_dns(self):
        tg = FakeTg(ips=["104.16.231.132", "104.16.230.132"])
        await self._run_keeper(tg)
        self.assertTrue(await self._until(lambda: self.app._webhook_ok))
        # sekali pasang, langsung dengan IP pertama: tak bergantung resolver Telegram
        self.assertEqual([c for c in tg.calls if c[0] == "set"], [("set", URL, "104.16.231.132")])

    async def test_falls_back_to_telegram_dns_when_the_fixed_ip_attempt_fails(self):
        tg = FakeTg(ips=["104.16.231.132"], sets=[False, True])
        await self._run_keeper(tg)
        self.assertTrue(await self._until(lambda: self.app._webhook_ok))
        self.assertEqual([c[2] for c in tg.calls if c[0] == "set"], ["104.16.231.132", None])

    async def test_without_a_public_ip_it_lets_telegram_resolve(self):
        tg = FakeTg(ips=[])
        await self._run_keeper(tg)
        self.assertTrue(await self._until(lambda: self.app._webhook_ok))
        self.assertEqual([c[2] for c in tg.calls if c[0] == "set"], [None])

    async def test_keeps_retrying_instead_of_giving_up(self):
        tg = FakeTg(sets=[False, False, False, True])
        await self._run_keeper(tg)
        self.assertTrue(await self._until(lambda: self.app._webhook_ok))
        self.assertEqual(sum(1 for c in tg.calls if c[0] == "set"), 4)

    async def test_reinstalls_when_webhook_disappears(self):
        good = {"url": f"{URL}/tg/{SECRET}"}
        tg = FakeTg(infos=[good, {"url": ""}, good])  # sehat, lalu hilang
        await self._run_keeper(tg)
        self.assertTrue(await self._until(lambda: sum(1 for c in tg.calls if c[0] == "set") >= 2))

    async def test_watch_returns_for_wrong_url_or_recent_delivery_error(self):
        self.app.tg = FakeTg(infos=[{"url": "https://lain.example/tg/x"}])
        await asyncio.wait_for(self.app._watch_webhook(URL, SECRET, every=0), 2)
        self.app.tg = FakeTg(infos=[{"url": f"{URL}/tg/{SECRET}", "last_error_date": time.time() - 5,
                                     "last_error_message": "Connection refused"}])
        await asyncio.wait_for(self.app._watch_webhook(URL, SECRET, every=300), 2)

    async def test_watch_keeps_watching_while_everything_is_fine(self):
        self.app.tg = FakeTg(infos=[{"url": f"{URL}/tg/{SECRET}", "last_error_date": time.time() - 99999}])
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.app._watch_webhook(URL, SECRET, every=300), 0.2)

    async def test_poll_uses_getupdates_until_webhook_is_ready(self):
        tg = FakeTg()
        self.app.tg = tg
        self.app._webhook_ok = False
        task = asyncio.ensure_future(self.app._poll())
        self.addCleanup(task.cancel)
        self.assertTrue(await self._until(lambda: ("poll",) in tg.calls))

    async def test_poll_stays_quiet_while_webhook_is_healthy(self):
        tg = FakeTg()
        self.app.tg = tg
        self.app._webhook_ok = True
        task = asyncio.ensure_future(self.app._poll())
        self.addCleanup(task.cancel)
        await self.real_sleep(0.05)  # sungguhan: beri kesempatan bila memang (salah) memanggil getUpdates
        self.assertNotIn(("poll",), tg.calls)


if __name__ == "__main__":
    unittest.main()
