"""Uji Pembuat Aplikasi HP: picu & pantau build APK lewat GitHub (tanpa jaringan)."""

import unittest
from unittest import mock

from itsme import config, hpbuild


def _resp(status=204, body=None):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    return r


class HpBuildTest(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(config, "GITHUB_TOKEN", "ghp_" + "x" * 36),
                  mock.patch.object(config, "HP_REPO", "OBKENTUNG01/itsme-hp")):
            p.start()
            self.addCleanup(p.stop)

    def test_not_configured(self):
        with mock.patch.object(config, "GITHUB_TOKEN", ""):
            self.assertFalse(hpbuild.configured())
            self.assertEqual(hpbuild.status()["configured"], False)
            with self.assertRaises(hpbuild.BuildError):
                hpbuild.trigger({"app_name": "X"})

    def test_trigger_sends_only_known_inputs(self):
        with mock.patch.object(hpbuild.httpx, "post", return_value=_resp(204)) as post:
            ok = hpbuild.trigger({"app_name": "Catatan", "accent": "#ffffff",
                                  "app_id": "com.x.y", "version_name": "1.0", "junk": "buang"})
        self.assertTrue(ok)
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["ref"], "main")
        self.assertEqual(set(body["inputs"]), {"app_name", "accent", "app_id", "version_name"})
        self.assertNotIn("junk", body["inputs"])
        self.assertTrue(post.call_args.args[0].endswith("/actions/workflows/build.yml/dispatches"))

    def test_trigger_rejected_token(self):
        with mock.patch.object(hpbuild.httpx, "post", return_value=_resp(403)):
            with self.assertRaises(hpbuild.BuildError):
                hpbuild.trigger({"app_name": "X"})

    def test_status_includes_run_and_apk(self):
        runs = _resp(200, {"workflow_runs": [{"status": "completed", "conclusion": "success",
                                              "html_url": "https://run", "created_at": "t"}]})
        rel = _resp(200, {"assets": [{"name": "itsme-hp.apk",
                                      "browser_download_url": "https://dl/itsme-hp.apk"}]})
        with mock.patch.object(hpbuild.httpx, "get", side_effect=[runs, rel]):
            s = hpbuild.status()
        self.assertTrue(s["configured"])
        self.assertEqual(s["run"]["conclusion"], "success")
        self.assertEqual(s["apk"], "https://dl/itsme-hp.apk")

    def test_latest_apk_none_when_no_release(self):
        with mock.patch.object(hpbuild.httpx, "get", return_value=_resp(404)):
            self.assertIsNone(hpbuild.latest_apk())


if __name__ == "__main__":
    unittest.main()
