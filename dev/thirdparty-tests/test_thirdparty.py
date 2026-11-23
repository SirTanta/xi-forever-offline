"""Offline checks for launcher_thirdparty.py: no network, a stand-in GitHub. Run: python test_thirdparty.py"""
import hashlib, io, json, os, shutil, sys, tempfile, unittest, zipfile
KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, KIT)
import launcher_thirdparty as tp


def zip_of(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class ThirdPartyTests(unittest.TestCase):
    def setUp(self):
        self.kit, self.ash = tempfile.mkdtemp(), tempfile.mkdtemp()
        shutil.copy(os.path.join(KIT, "thirdparty.json"), self.kit)
        self.items = {i["id"]: i for i in tp.load_manifest(self.kit)}

    def tearDown(self):
        shutil.rmtree(self.kit, ignore_errors=True)
        shutil.rmtree(self.ash, ignore_errors=True)

    def fake(self, release, files):
        def fetch(url, timeout=60):
            if url.startswith(tp.API):
                return json.dumps(release).encode()
            return files[url]
        return fetch

    def test_every_item_is_complete(self):
        for item in self.items.values():
            for key in ("id", "name", "author", "license", "homepage", "source"):
                self.assertIn(key, item, item.get("id"))
            if item["source"]["type"] not in ("page", "installer", "ollama_model"):
                self.assertTrue(item.get("files"), item["id"])

    def test_release_with_checksum(self):
        dll = b"MZ fake dll"
        release = {"tag_name": "v9", "assets": [{"name": "XIUIplus.dll", "browser_download_url": "u/dll"},
                                                {"name": "SHA256SUMS.txt", "browser_download_url": "u/sums"}]}
        good = self.fake(release, {"u/dll": dll, "u/sums": (hashlib.sha256(dll).hexdigest() + "  XIUIplus.dll\n").encode()})
        self.assertEqual(tp.install(self.items["xiuiplus"], self.kit, self.ash, fetch=good, log=lambda m: None), "v9")
        with open(os.path.join(self.ash, "plugins", "XIUIplus.dll"), "rb") as f:
            self.assertEqual(f.read(), dll)
        bad = self.fake(release, {"u/dll": dll + b"!", "u/sums": (hashlib.sha256(dll).hexdigest() + "  XIUIplus.dll\n").encode()})
        with self.assertRaises(RuntimeError):
            tp.install(self.items["xiuiplus"], self.kit, self.ash, fetch=bad, log=lambda m: None)

    def test_archive_keeps_settings_and_backs_up(self):
        os.makedirs(os.path.join(self.ash, "config", "boot"))
        with open(os.path.join(self.ash, "config", "boot", "my.ini"), "w") as f:
            f.write("mine")
        with open(os.path.join(self.ash, "Ashita-cli.exe"), "w") as f:
            f.write("old")
        os.makedirs(os.path.join(self.ash, "config", "ashita"))
        for name, text in (("ashita.offsets.ini", "old offsets"), ("custom.offsets.ini", "my offsets")):
            with open(os.path.join(self.ash, "config", "ashita", name), "w") as f:
                f.write(text)
        data = zip_of({"Ashita-v4beta-x/Ashita-cli.exe": "new", "Ashita-v4beta-x/config/boot/my.ini": "theirs",
                       "Ashita-v4beta-x/addons/fps/fps.lua": "fps",
                       "Ashita-v4beta-x/config/ashita/ashita.offsets.ini": "new offsets",
                       "Ashita-v4beta-x/config/ashita/custom.offsets.ini": "blank"})
        ref = self.items["ashita"]["source"]["ref"]
        url = "https://github.com/AshitaXI/Ashita-v4beta/archive/%s.zip" % ref
        tp.install(self.items["ashita"], self.kit, self.ash, fetch=self.fake({}, {url: data}), log=lambda m: None)
        read = lambda *p: open(os.path.join(self.ash, *p)).read()
        self.assertEqual((read("Ashita-cli.exe"), read("config", "boot", "my.ini"), read("addons", "fps", "fps.lua")), ("new", "mine", "fps"))
        # Ashita's own offsets follow its version (the November 2026 client fix); the player's custom file stays
        self.assertEqual((read("config", "ashita", "ashita.offsets.ini"), read("config", "ashita", "custom.offsets.ini")),
                         ("new offsets", "my offsets"))
        backups = os.listdir(os.path.join(self.kit, "backups", "thirdparty"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(tp.status(self.items["ashita"], self.kit, self.ash), (True, ref))

    def test_model_status_reads_ollama_list(self):
        tp.ollama_models = lambda run=None: ["gemma4:12b", "mistral-nemo:latest"]
        self.assertEqual(tp.status(self.items["aimodel"], self.kit, self.ash), (True, "gemma4:12b"))
        tp.ollama_models = lambda run=None: []
        self.assertEqual(tp.status(self.items["aimodel"], self.kit, self.ash)[0], False)

    def test_nothing_outside_its_folder(self):
        with self.assertRaises(RuntimeError):
            tp.plan({"name": "bad", "files": [["a/", "../../evil/"]]}, ["a/x.lua"])

    def test_page_items_are_not_downloaded(self):
        with self.assertRaises(ValueError):
            tp.resolve(self.items["journal"])

    # ----- channels: a "beta" item is for players who switched on "Get test builds" -----
    BETA = {"id": "betaaddon", "name": "Beta addon", "author": "a", "license": "l", "homepage": "h", "channel": "beta",
            "into": "ashita", "check": "addons/betaaddon/betaaddon.lua", "files": [["betaaddon/", "addons/betaaddon/"]],
            "source": {"type": "github_release", "repo": "x/betaaddon", "tag": "latest", "asset": "^betaaddon\.zip$"}}

    def with_beta(self, test_builds=None):
        with open(os.path.join(self.kit, "thirdparty.json"), encoding="utf-8-sig") as f:
            data = json.load(f)
        data["items"].append(self.BETA)
        with open(os.path.join(self.kit, "thirdparty.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        if test_builds is not None:
            with open(os.path.join(self.kit, "config.json"), "w") as f:
                json.dump({"test_builds": test_builds}, f)
        return [i["id"] for i in tp.load_manifest(self.kit)]

    def test_beta_hidden_by_default(self):
        self.assertNotIn("betaaddon", self.with_beta())
        self.assertNotIn("betaaddon", self.with_beta(False))

    def test_beta_shown_with_test_builds(self):
        self.assertIn("betaaddon", self.with_beta(True))

    def test_beta_stays_listed_once_installed(self):
        self.with_beta(False)
        with open(os.path.join(self.kit, "thirdparty-installed.json"), "w") as f:
            json.dump({"betaaddon": {"version": "v1.0.0-beta.1"}}, f)
        self.assertIn("betaaddon", [i["id"] for i in tp.load_manifest(self.kit)])

    def test_beta_takes_the_newest_prerelease(self):
        releases = [{"tag_name": "v1.0.0-beta.2", "prerelease": True, "assets": [{"name": "betaaddon.zip", "browser_download_url": "u/b2"}]},
                    {"tag_name": "v0.9.0", "assets": [{"name": "betaaddon.zip", "browser_download_url": "u/s"}]}]
        fetch = lambda url, timeout=60: json.dumps(releases).encode()
        self.assertEqual(tp.resolve(self.BETA, fetch)["version"], "v1.0.0-beta.2")
        self.assertEqual(tp.latest_version(self.BETA, fetch), "v1.0.0-beta.2")
        stable = dict(self.BETA, channel="stable")
        one = lambda url, timeout=60: json.dumps(releases[1]).encode()
        self.assertEqual(tp.latest_version(stable, one), "v0.9.0")


if __name__ == "__main__":
    unittest.main(verbosity=1)
