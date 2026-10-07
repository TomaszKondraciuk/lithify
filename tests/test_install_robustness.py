"""What makes the first install work on any computer: a published bundle before a build, the build's
tests on this computer's own CPU, and a clear answer when the speaker cannot reach this computer."""
import unittest
from unittest import mock

from lithify import bundle, cli, device, hostos

RELEASE = {"release": {"url": "https://example.org/releases/latest/download"}}


class BundleSourceTest(unittest.TestCase):
    def test_a_published_bundle_comes_first(self):
        with mock.patch.object(bundle, "load_pins", return_value=RELEASE), \
                mock.patch.object(bundle, "fetch") as fetch, mock.patch.object(bundle, "build") as build:
            cli.get_bundle()
        fetch.assert_called_once_with(RELEASE["release"]["url"])
        build.assert_not_called()

    def test_a_release_that_cannot_be_downloaded_is_built_here(self):
        with mock.patch.object(bundle, "load_pins", return_value=RELEASE), \
                mock.patch.object(bundle, "fetch", side_effect=OSError("no route to host")), \
                mock.patch.object(bundle, "build") as build, mock.patch.object(cli, "say"):
            cli.get_bundle()
        build.assert_called_once_with()

    def test_without_a_release_or_when_asked_to_it_is_built(self):
        with mock.patch.object(bundle, "load_pins", return_value={"release": {"url": ""}}), \
                mock.patch.object(bundle, "fetch") as fetch, mock.patch.object(bundle, "build") as build:
            cli.get_bundle()
        fetch.assert_not_called()
        build.assert_called_once_with()
        with mock.patch.object(bundle, "load_pins", return_value=RELEASE), \
                mock.patch.object(bundle, "fetch") as fetch, mock.patch.object(bundle, "build") as build:
            cli.get_bundle(force_build=True)
        fetch.assert_not_called()
        build.assert_called_once_with()


class HostTripleTest(unittest.TestCase):
    def test_the_agent_tests_run_on_the_computers_own_cpu(self):
        arm = "rustc 1.99.0 (0123abcd 2026-09-01)\nbinary: rustc\nhost: aarch64-unknown-linux-gnu\nrelease: 1.99.0\n"
        self.assertEqual(bundle.host_triple(arm), "aarch64-unknown-linux-gnu")
        self.assertEqual(bundle.host_triple("rustc 1.99.0 (0123abcd 2026-09-01)\n"), "x86_64-unknown-linux-gnu")


class ReachTest(unittest.TestCase):
    def setUp(self):
        self.d = device.Device.__new__(device.Device)  # (its console is replaced below)

    def test_the_speaker_says_whether_it_can_download_from_here(self):
        with mock.patch.object(device.Device, "exec", return_value="HTTP200") as ex:
            self.assertEqual(self.d.can_download("http://10.1.2.3:18096/VERSIONS"), (True, "HTTP200"))
        self.assertIn("http://10.1.2.3:18096/VERSIONS", ex.call_args.args[0])
        said = "curl: (28) Connection timed out after 8001 milliseconds\nHTTP000"
        with mock.patch.object(device.Device, "exec", return_value=said):
            reachable, why = self.d.can_download("http://10.1.2.3:18096/VERSIONS")
        self.assertFalse(reachable)
        self.assertIn("timed out", why)

    def test_the_advice_fits_the_system_and_the_network(self):
        with mock.patch.object(hostos, "WINDOWS", True), mock.patch.object(hostos, "MACOS", False):
            self.assertIn("Private", hostos.firewall_advice("10.1.2.3"))
        with mock.patch.object(hostos, "WINDOWS", False), mock.patch.object(hostos, "MACOS", True):
            self.assertIn("Allow", hostos.firewall_advice())
        with mock.patch.object(hostos, "WINDOWS", False), mock.patch.object(hostos, "MACOS", False):
            linux = hostos.firewall_advice("10.1.2.3")
            fallback = hostos.firewall_advice("not-an-address")
        self.assertIn("10.1.2.0/24", linux)
        self.assertIn("18096:18099", linux)
        self.assertIn("192.168.0.0/16", fallback)
        self.assertIn("VPN", linux)


if __name__ == "__main__":
    unittest.main()
