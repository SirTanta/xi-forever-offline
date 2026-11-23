"""The launcher's side of the Revamped switch (launcher_revamped.py). Run: python test_launcher_revamped.py
Uses a throw-away fake Ashita folder in the temp directory; never touches a real install.
"""
import os
import shutil
import sys
import tempfile

KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, KIT)
import launcher_core as core
import launcher_revamped as rv

passed = failed = 0


def check(name, ok):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL:", name)


def fake_ashita():
    root = tempfile.mkdtemp(prefix="rvl_")
    os.makedirs(os.path.join(root, "config", "boot"))
    os.makedirs(os.path.join(root, "scripts"))
    os.makedirs(os.path.join(root, "addons"))
    with open(os.path.join(root, "config", "boot", "t.ini"), "w") as f:
        f.write("[ashita.boot]\nscript      = xiforever.txt\n")
    with open(os.path.join(root, "scripts", "xiforever.txt"), "w") as f:
        f.write("/load addons\n/addon load squadpanel\n/addon load Gambits\n/addon load servermenu\n")
    return root


def script(root):
    with open(os.path.join(root, "scripts", "xiforever.txt")) as f:
        return f.read()


# parse
check("parse yes", rv.parse("asked=1\nuse_revamped=1\n") == {"asked": True, "use": True})
check("parse no", rv.parse("asked=1\r\nuse_revamped=0\r\n") == {"asked": True, "use": False})
check("parse empty", rv.parse("") == {"asked": False, "use": None})
check("parse junk", rv.parse("hello\n=\n") == {"asked": False, "use": None})

# write then read, the file the game reads
a = fake_ashita()
check("never asked", rv.read_choice(a) == {"asked": False, "use": None})
path = rv.write_choice(a, True)
check("path is the shared one", path.endswith(os.path.join("config", "addons", "revamped", "choice.txt")))
check("read back yes", rv.read_choice(a) == {"asked": True, "use": True})
with open(path) as f:
    check("file text is what rv_switch.lua parses", f.read() == "asked=1\nuse_revamped=1\n")

# shells: copied when no package, never over a package
a = fake_ashita()
done = rv.install_shells(KIT, a)
check("both shells copied", sorted(done) == ["GambitsRevamped", "SquadsRevamped"])
check("shell files there", os.path.isfile(os.path.join(a, "addons", "SquadsRevamped", "SquadsRevamped.lua"))
      and os.path.isfile(os.path.join(a, "addons", "GambitsRevamped", "rv_switch.lua")))
pkg = os.path.join(a, "addons", "SquadsRevamped")
with open(os.path.join(pkg, "sr_window.lua"), "w") as f:
    f.write("-- the package\n")
with open(os.path.join(pkg, "SquadsRevamped.lua"), "w") as f:
    f.write("-- the package main\n")
done = rv.install_shells(KIT, a)
check("package folder is skipped", done == ["GambitsRevamped"])
with open(os.path.join(pkg, "SquadsRevamped.lua")) as f:
    check("package main file untouched", f.read() == "-- the package main\n")

# enable once, and the choice applied to the start script
a = fake_ashita()
rv.install_shells(KIT, a)
m = core.ScriptManager(a, "t.ini")
m.prepare()
check("enabled first time", rv.enable_shells_once(a, m, log=lambda *_: None) is True)
check("both load lines in the script", "/addon load GambitsRevamped" in script(a) and "/addon load SquadsRevamped" in script(a))
check("not a second time", rv.enable_shells_once(a, m, log=lambda *_: None) is False)
m.set("addon", "SquadsRevamped", False)
rv.enable_shells_once(a, m, log=lambda *_: None)
check("a player's off stays off", "SquadsRevamped" not in script(a))

# yes with only shells: nothing off. yes with a package: old ones off, files kept. no: nothing.
rv.write_choice(a, True)
check("yes, shells only: nothing off", rv.apply_choice(a, m, log=lambda *_: None) == [] and "squadpanel" in script(a))
with open(os.path.join(a, "addons", "GambitsRevamped", "gr_editor.lua"), "w") as f:
    f.write("-- package\n")
os.makedirs(os.path.join(a, "addons", "squadpanel"))
with open(os.path.join(a, "addons", "squadpanel", "squadpanel.lua"), "w") as f:
    f.write("-- old\n")
off = rv.apply_choice(a, m, log=lambda *_: None)
check("yes with package: old two off", sorted(off) == ["Gambits", "squadpanel"])
s = script(a)
check("script keeps the others", "servermenu" in s and "/addon load squadpanel" not in s and "/addon load Gambits\n" not in s)
check("old addon folder kept", os.path.isfile(os.path.join(a, "addons", "squadpanel", "squadpanel.lua")))

a = fake_ashita()
rv.install_shells(KIT, a)
with open(os.path.join(a, "addons", "GambitsRevamped", "gr_editor.lua"), "w") as f:
    f.write("-- package\n")
m = core.ScriptManager(a, "t.ini")
m.prepare()
before = script(a)
rv.write_choice(a, False)
check("no: nothing changes", rv.apply_choice(a, m, log=lambda *_: None) == [] and script(a) == before)

# the whole apply() on a fake install
a = fake_ashita()
res = rv.apply(KIT, a, "t.ini", log=lambda *_: None)
check("apply: copied and enabled", len(res["copied"]) == 2 and res["enabled"] is True and res["off"] == [])
check("apply: old add-ons still on", "/addon load squadpanel" in script(a))
check("launcher knows them as ours", {"gambitsrevamped", "squadsrevamped"} <= core.OWN_ADDONS)

for d in ():
    shutil.rmtree(d, ignore_errors=True)
print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
