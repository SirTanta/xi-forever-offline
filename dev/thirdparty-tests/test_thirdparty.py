"""Offline checks for launcher_thirdparty.py: no network, a stand-in GitHub. Run: python test_thirdparty.py"""
import hashlib, io, json, os, shutil, sys, tempfile, time, unittest, urllib.error, zipfile
KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, KIT)
import launcher_thirdparty as tp


def zip_of(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class FakeResponse:
    """A stand-in for an open connection: remembers how much was asked for in one read (a read with no size would mean
    the whole file in memory)."""
    def __init__(self, data, headers=None):
        self.buf, self.reads, self.headers = io.BytesIO(data), [], headers or {}

    def read(self, size=None):
        self.reads.append(size)
        return self.buf.read() if size is None else self.buf.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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
            "source": {"type": "github_release", "repo": "x/betaaddon", "tag": "latest", "asset": "^betaaddon[.]zip$"}}

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

    def test_beta_group_needs_a_sign_up(self):
        self.BETA = dict(self.BETA, group="gear-gambits")
        self.assertNotIn("betaaddon", self.with_beta(True))                       # test builds on, no group chosen
        for value, shown in (("gear-gambits", True), ("Vault, Gear-Gambits", True), (["gear-gambits"], True), ("vault", False)):
            with open(os.path.join(self.kit, "config.json"), "w") as f:
                json.dump({"test_builds": True, "beta_groups": value}, f)
            self.assertEqual("betaaddon" in [i["id"] for i in tp.load_manifest(self.kit)], shown, value)
        with open(os.path.join(self.kit, "config.json"), "w") as f:
            json.dump({"test_builds": False, "beta_groups": "gear-gambits"}, f)     # test builds off beats the sign-up
        self.assertNotIn("betaaddon", [i["id"] for i in tp.load_manifest(self.kit)])

    def test_beta_takes_the_newest_prerelease(self):
        releases = [{"tag_name": "v1.0.0-beta.2", "prerelease": True, "assets": [{"name": "betaaddon.zip", "browser_download_url": "u/b2"}]},
                    {"tag_name": "v0.9.0", "assets": [{"name": "betaaddon.zip", "browser_download_url": "u/s"}]}]
        fetch = lambda url, timeout=60: json.dumps(releases).encode()
        self.assertEqual(tp.resolve(self.BETA, fetch)["version"], "v1.0.0-beta.2")
        self.assertEqual(tp.latest_version(self.BETA, fetch), "v1.0.0-beta.2")
        stable = dict(self.BETA, channel="stable")
        one = lambda url, timeout=60: json.dumps(releases[1]).encode()
        self.assertEqual(tp.latest_version(stable, one), "v0.9.0")

    def test_pinned_item_is_never_offered_a_newer_version(self):
        # Chains is pinned to the tested v0.86: asking GitHub for "latest" showed "(newer: ...)" for ever, and
        # Reinstall / update put v0.86 back
        asked = []
        fetch = lambda url, timeout=60: asked.append(url) or b'{"tag_name": "v9.9"}'
        self.assertEqual(self.items["chains"]["source"]["tag"], "v0.86")
        self.assertIsNone(tp.latest_version(self.items["chains"], fetch))
        self.assertEqual(asked, [])
        self.assertEqual(tp.latest_version(self.items["xiuiplus"], fetch), "v9.9")

    def test_damaged_state_is_kept_not_overwritten(self):
        path = tp.state_path(self.kit)
        with open(path, "w") as f:
            f.write('{"server": {"version": "v1"')           # cut off mid-write
        self.assertEqual(tp.load_state(self.kit), {})
        self.assertFalse(os.path.exists(path))
        with open(path + ".bad") as f:
            self.assertIn("server", f.read())

    def test_state_save_leaves_no_temp_file(self):
        tp._save_state(self.kit, {"a": {"version": "1"}})
        self.assertEqual(tp.load_state(self.kit), {"a": {"version": "1"}})
        self.assertFalse(os.path.exists(tp.state_path(self.kit) + ".tmp"))

    def test_missing_checksum_file_is_a_clear_refusal(self):
        release = {"tag_name": "v9", "assets": [{"name": "XIUIplus.dll", "browser_download_url": "u/dll"}]}
        with self.assertRaises(RuntimeError) as caught:
            tp.install(self.items["xiuiplus"], self.kit, self.ash, fetch=self.fake(release, {"u/dll": b"MZ"}), log=lambda m: None)
        self.assertIn("SHA256SUMS.txt", str(caught.exception))
        self.assertFalse(os.path.exists(os.path.join(self.ash, "plugins", "XIUIplus.dll")))

    def test_downloaded_zip_is_closed_after_install(self):
        # on Windows an open zip cannot be deleted: the downloads folder could not be cleaned up
        url = "https://github.com/AshitaXI/Ashita-v4beta/archive/%s.zip" % self.items["ashita"]["source"]["ref"]
        data = zip_of({"Ashita-v4beta-x/Ashita-cli.exe": "new"})
        tp.install(self.items["ashita"], self.kit, self.ash, fetch=self.fake({}, {url: data}), log=lambda m: None)
        folder = os.path.join(self.kit, "downloads", "ashita")
        for name in os.listdir(folder):
            os.remove(os.path.join(folder, name))          # would raise PermissionError on Windows if still open

    def install_chains(self, files):
        release = {"tag_name": "v0.86", "assets": [{"name": "Chains-v0.86-Phoenix.zip", "browser_download_url": "u/z"}]}
        fetch = self.fake(release, {"u/z": zip_of(files)})
        tp.install(self.items["chains"], self.kit, self.ash, fetch=fetch, log=lambda m: None)

    def test_uninstall_removes_what_was_installed_and_keeps_the_rest(self):
        self.install_chains({"chains/chains.lua": "x", "chains/data/a.lua": "y"})
        mine = os.path.join(self.ash, "addons", "chains", "my-notes.txt")
        with open(mine, "w") as f:
            f.write("mine")
        self.assertEqual(tp.uninstall(self.items["chains"], self.kit, self.ash, log=lambda m: None), 2)
        self.assertFalse(os.path.exists(os.path.join(self.ash, "addons", "chains", "chains.lua")))
        self.assertTrue(os.path.exists(mine))                      # not ours: stays
        self.assertNotIn("chains", tp.load_state(self.kit))
        saved = os.listdir(os.path.join(self.kit, "backups", "thirdparty"))
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].startswith("chains-uninstall-"))

    def test_uninstall_leaves_no_empty_folders(self):
        self.install_chains({"chains/chains.lua": "x", "chains/data/a.lua": "y"})
        tp.uninstall(self.items["chains"], self.kit, self.ash, log=lambda m: None)
        self.assertFalse(os.path.exists(os.path.join(self.ash, "addons", "chains")))

    def test_uninstall_refuses_without_a_record_and_for_required_items(self):
        os.makedirs(os.path.join(self.ash, "addons", "chains"))
        with self.assertRaises(RuntimeError):
            tp.uninstall(self.items["chains"], self.kit, self.ash, log=lambda m: None)
        self.assertTrue(os.path.isdir(os.path.join(self.ash, "addons", "chains")))
        with self.assertRaises(RuntimeError):
            tp.uninstall(self.items["ashita"], self.kit, self.ash, log=lambda m: None)

    def test_uninstall_refuses_a_tampered_list(self):
        self.install_chains({"chains/chains.lua": "x"})
        state = tp.load_state(self.kit)
        state["chains"]["files"] = ["../../outside.txt"]
        tp._save_state(self.kit, state)
        with self.assertRaises(RuntimeError):
            tp.uninstall(self.items["chains"], self.kit, self.ash, log=lambda m: None)

    def test_update_keeps_the_list_of_older_files(self):
        self.install_chains({"chains/chains.lua": "x", "chains/old.lua": "y"})
        self.install_chains({"chains/chains.lua": "x2"})
        self.assertEqual(sorted(tp.load_state(self.kit)["chains"]["files"]), ["addons/chains/chains.lua", "addons/chains/old.lua"])

    def test_gambits_revamped_entry_installs_the_addon_only(self):
        with open(os.path.join(self.kit, "config.json"), "w") as f:
            json.dump({"test_builds": True, "beta_groups": ["gambits-core"]}, f)
        item = {i["id"]: i for i in tp.load_manifest(self.kit)}["gambits-revamped"]
        self.assertEqual(tp.channel_of(item), "beta")
        top = "GambitsRevamped-1.0.0-abc1234/"
        zipped = zip_of({top + "Ashita/addons/GambitsRevamped/GambitsRevamped.lua": "addon",
                         top + "Ashita/addons/GambitsRevamped/helper/x.lua": "x",
                         top + "server/modules/custom/lua/gambits_own.lua": "engine", top + "README.md": "r"})
        name = "GambitsRevamped-1.0.0-abc1234.zip"
        release = {"tag_name": "v1.0.0-beta.1", "prerelease": True,
                   "assets": [{"name": name, "browser_download_url": "u/z"}, {"name": "SHA256SUMS.txt", "browser_download_url": "u/s"}]}
        sums = ("%s  %s\n" % (hashlib.sha256(zipped).hexdigest(), name)).encode()
        fetch = lambda url, timeout=60: json.dumps([release]).encode() if url.startswith(tp.API) else {"u/z": zipped, "u/s": sums}[url]
        self.assertEqual(tp.install(item, self.kit, self.ash, fetch=fetch, log=lambda m: None), "v1.0.0-beta.1")
        self.assertTrue(os.path.isfile(os.path.join(self.ash, "addons", "GambitsRevamped", "GambitsRevamped.lua")))
        self.assertTrue(os.path.isfile(os.path.join(self.ash, "addons", "GambitsRevamped", "helper", "x.lua")))
        self.assertEqual(sorted(os.listdir(self.ash)), ["addons"])             # the server files stay out of Ashita
        bad = lambda url, timeout=60: sums if url == "u/s" else (zipped + b"!" if url == "u/z" else fetch(url))
        with self.assertRaises(RuntimeError):
            tp.install(item, self.kit, self.ash, fetch=bad, log=lambda m: None)

    # ---- (v0.3.3 launcher fixes) a download goes to disk a piece at a time, never whole into memory ----
    def with_connection(self, answers):
        """tp._open_url answers {url: bytes or an exception}; returns the list of FakeResponses handed out."""
        handed, real = [], tp._open_url

        def fake(url, timeout=60, headers=None):
            answer = answers[url]
            if isinstance(answer, BaseException):
                raise answer
            response = FakeResponse(answer)
            handed.append(response)
            return response
        tp._open_url = fake
        self.addCleanup(setattr, tp, "_open_url", real)
        return handed

    def test_download_is_read_in_pieces_not_whole(self):
        data = os.urandom(3 * (1 << 20) + 17)
        handed = self.with_connection({"u/big": data})
        found = {"url": "u/big", "name": "big.zip", "sha256": hashlib.sha256(data).hexdigest()}
        path = tp.download({"id": "big", "name": "Big"}, self.kit, found, tp._get)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), data)
        self.assertNotIn(None, handed[0].reads)                       # never "read everything"
        self.assertLessEqual(max(r for r in handed[0].reads), 1 << 20)
        self.assertGreaterEqual(len(handed[0].reads), 4)
        self.assertEqual(found["sha256_actual"], hashlib.sha256(data).hexdigest())
        self.assertFalse(os.path.exists(path + ".part"))

    def test_streamed_download_that_does_not_match_leaves_nothing(self):
        self.with_connection({"u/x": b"tampered"})
        found = {"url": "u/x", "name": "x.zip", "sha256": hashlib.sha256(b"the real one").hexdigest()}
        with self.assertRaises(RuntimeError) as caught:
            tp.download({"id": "x", "name": "Xthing"}, self.kit, found, tp._get)
        self.assertIn("does not match its published checksum", str(caught.exception))
        self.assertEqual(os.listdir(os.path.join(self.kit, "downloads", "x")), [])      # no file, no .part

    def test_streamed_download_that_breaks_half_way_leaves_nothing(self):
        class Breaks(FakeResponse):
            def read(self, size=None):
                data = super().read(size)
                if len(self.reads) > 1:
                    raise OSError("connection reset")
                return data
        real = tp._open_url
        tp._open_url = lambda url, timeout=60, headers=None: Breaks(b"y" * 5000)
        self.addCleanup(setattr, tp, "_open_url", real)
        with self.assertRaises(OSError):
            tp.stream_to_file("u/y", os.path.join(self.kit, "y.zip"), chunk=1000)
        self.assertEqual(os.listdir(self.kit).count("y.zip"), 0)
        self.assertEqual(os.listdir(self.kit).count("y.zip.part"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
