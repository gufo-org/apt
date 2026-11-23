import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import aptlib  # noqa: E402

KEYRING = os.path.join(ROOT, "static", "keyring")


def run(*args, **kwargs):
    return subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, cwd=ROOT, **kwargs
    )


class AptlibTest(unittest.TestCase):
    def test_build_deb_round_trips_control(self):
        control = "Package: demo\nVersion: 1.0\nArchitecture: all\nDescription: x\n more"
        deb = aptlib.build_deb(control + "\n", [("usr/", 0o755, None)], mtime=1)
        self.assertEqual(aptlib.deb_control(deb), control)
        self.assertEqual(deb, aptlib.build_deb(control + "\n", [("usr/", 0o755, None)], mtime=1))

    def test_pool_mapping_only_accepts_gufo_org_debs(self):
        url = "https://github.com/gufo-org/gufo/releases/download/v1.0.0/gufo_1.0.0-1_amd64.deb"
        path = aptlib.pool_path(url)
        self.assertEqual(path, "pool/gufo/v1.0.0/gufo_1.0.0-1_amd64.deb")
        self.assertEqual(aptlib.release_url(path), url)
        for bad in (
            url.replace("gufo-org", "someone-else"),
            url.replace(".deb", ".zip"),
            url.replace("/v1.0.0/", "/../"),
            "http://github.com/gufo-org/gufo/releases/download/v1/a.deb",
        ):
            with self.assertRaises(ValueError):
                aptlib.pool_path(bad)
        with self.assertRaises(ValueError):
            aptlib.release_url("pool/gufo/v1/extra/a.deb")

    def test_parse_paragraph_keeps_continuations(self):
        fields = aptlib.parse_paragraph("Package: a\nDescription: s\n line one\n .\n line two")
        self.assertEqual(fields["Description"], "s\n line one\n .\n line two")


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.manifests = os.path.join(self.tmp, "packages")
        self.keyring = os.path.join(KEYRING, sorted(os.listdir(KEYRING))[-1])

    def add(self, *extra):
        return run("tools/add-package", "--keyring", self.keyring, "--manifests", self.manifests, *extra)

    def test_add_is_idempotent_and_immutable(self):
        self.assertEqual(self.add().returncode, 0)
        again = self.add()
        self.assertEqual(again.returncode, 0)
        self.assertIn("nothing to do", again.stdout)
        path = os.path.join(self.manifests, "gufo-archive-keyring.json")
        with open(path) as handle:
            manifest = json.load(handle)
        manifest["entries"][0]["sha256"] = "0" * 64
        with open(path, "w") as handle:
            json.dump(manifest, handle)
        changed = self.add()
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn("different content", changed.stderr)

    def test_add_checks_expected_hash(self):
        result = self.add("--sha256", "0" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mismatch", result.stderr)

    def test_index_hashes_match_files(self):
        self.assertEqual(self.add().returncode, 0)
        site = os.path.join(self.tmp, "site")
        result = run("tools/build-index", site, "--manifests", self.manifests)
        self.assertEqual(result.returncode, 0, result.stderr)
        dist = os.path.join(site, "dists", "trixie")
        with open(os.path.join(dist, "Release")) as handle:
            release = handle.read()
        lines = release.split("SHA256:\n", 1)[1].strip().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            digest, size, name = line.split()
            with open(os.path.join(dist, name), "rb") as handle:
                data = handle.read()
            self.assertEqual((aptlib.sha256(data), len(data)), (digest, int(size)))
        with open(os.path.join(dist, "main", "binary-amd64", "Packages")) as handle:
            packages = handle.read()
        self.assertIn("Package: gufo-archive-keyring\n", packages)
        self.assertIn("Filename: keyring/", packages)
        self.assertTrue(os.path.exists(os.path.join(site, "gufo-archive-keyring.deb")))

    def test_manifest_matches_static_keyring_files(self):
        with open(os.path.join(ROOT, "packages", "gufo-archive-keyring.json")) as handle:
            entries = json.load(handle)["entries"]
        for entry in entries:
            with open(os.path.join(ROOT, "static", entry["filename"]), "rb") as handle:
                self.assertEqual(aptlib.sha256(handle.read()), entry["sha256"])


if __name__ == "__main__":
    unittest.main()
