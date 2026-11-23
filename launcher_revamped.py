"""The saved Revamped switch, the launcher's side (the game's side is addons-src/*Revamped/rv_switch.lua).

Why it exists: Gambits Revamped and Squads Revamped are separate packages that come after this kit. The kit ships two empty
shell add-ons (GambitsRevamped, SquadsRevamped) so the packages plug in over them, and one small switch file both the
game and this launcher read and write:

    <Ashita>\\config\\addons\\revamped\\choice.txt        asked=1 / use_revamped=1 or 0

use_revamped=1 means: the old Squads window (squadpanel) and the old Gambits add-on (Gambits) are off at start, Revamped is on.
Nothing is ever deleted: the old add-ons stay in Ashita's addons folder and the Addons page can switch them back on.

What the launcher does with it (apply(), called once per start from launcher_gui.pyw):
  1. install_shells: copies the two shells into Ashita, except where a Revamped package is already there (a package file
     is never overwritten by a shell: gr_editor.lua marks the Gambits package, sr_window.lua the Squads package).
  2. enable_shells_once: loads the two shells at start once (marker file), so the question can appear when a package comes.
  3. apply_choice: use_revamped=1 and a package present -> /addon load lines for squadpanel and Gambits are taken out of the
     start script; use_revamped=0 or never asked -> nothing is touched.
Pure stdlib, no screen needed.
"""
import os
import shutil
import time

SHELLS = ("GambitsRevamped", "SquadsRevamped")
PACKAGE_MARKERS = {"GambitsRevamped": "gr_editor.lua", "SquadsRevamped": "sr_window.lua"}
OLD_ADDONS = ("squadpanel", "Gambits")
IGNORE = ("*.before-*", "*.bak")


def choice_path(ashita_dir):
    return os.path.join(ashita_dir, "config", "addons", "revamped", "choice.txt")


def parse(text):
    """{'asked': bool, 'use': True/False/None} from the text of choice.txt (the same rules as rv_switch.lua)."""
    out = {"asked": False, "use": None}
    for line in (text or "").splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip().split()[0] if value.strip() else ""
        if key == "asked":
            out["asked"] = value == "1"
        elif key == "use_revamped":
            out["use"] = True if value == "1" else False if value == "0" else None
    return out


def read_choice(ashita_dir):
    try:
        with open(choice_path(ashita_dir), encoding="utf-8-sig") as handle:
            return parse(handle.read())
    except OSError:
        return {"asked": False, "use": None}


def write_choice(ashita_dir, use):
    """What the question and the launcher's switch both write. Returns the path."""
    path = choice_path(ashita_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("asked=1\nuse_revamped=%d\n" % (1 if use else 0))
    os.replace(tmp, path)
    return path


def package_present(ashita_dir, name=None):
    """True when a Revamped package (not only a shell) is installed: for one add-on name, or for either."""
    names = [name] if name else list(SHELLS)
    return any(os.path.isfile(os.path.join(ashita_dir, "addons", n, PACKAGE_MARKERS[n])) for n in names)


def install_shells(kit, ashita_dir):
    """Copies addons-src\\GambitsRevamped and SquadsRevamped into Ashita, but never over a package. Returns what it copied."""
    copied = []
    for name in SHELLS:
        src = os.path.join(kit, "addons-src", name)
        dst = os.path.join(ashita_dir, "addons", name)
        if not os.path.isdir(src) or package_present(ashita_dir, name):
            continue
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*IGNORE))
        copied.append(name)
    return copied


def enable_shells_once(ashita_dir, manager, log=print):
    """Loads the shells at start, once per install (a player who switches them off in OPTIONS > Addons keeps them off).
    manager: launcher_core.ScriptManager (already prepared). Returns True when it changed the script."""
    marker = os.path.join(ashita_dir, "config", "addons", "revamped", "shells-enabled-once.txt")
    if os.path.exists(marker):
        return False
    have = {a.lower() for a in manager.enabled("addon")}
    changed = False
    for name in SHELLS:
        if os.path.isdir(os.path.join(ashita_dir, "addons", name)) and name.lower() not in have:
            manager.set("addon", name, True)
            changed = True
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
    if changed:
        log("Revamped shells are loaded (they draw nothing until a Revamped package is installed).")
    return changed


def apply_choice(ashita_dir, manager, log=print):
    """use_revamped=1 and a package present: the old Squads window and old Gambits are taken out of the start script.
    Returns the names switched off. A No, or never asked, changes nothing."""
    choice = read_choice(ashita_dir)
    if choice["use"] is not True or not package_present(ashita_dir):
        return []
    have = {a.lower() for a in manager.enabled("addon")}
    off = []
    for name in OLD_ADDONS:
        if name.lower() in have:
            manager.set("addon", name, False)
            off.append(name)
    if off:
        log("Revamped is on: the old " + " and ".join(off) + " add-on(s) are off (still installed; OPTIONS > Addons switches them back).")
    return off


def apply(kit, ashita_dir, boot_name, log=print):
    """Everything above, in order. Never raises for a missing piece: returns a dict of what happened."""
    import launcher_core as core
    done = {"copied": install_shells(kit, ashita_dir), "enabled": False, "off": []}
    if not os.path.isdir(os.path.join(ashita_dir, "addons")):
        return done
    manager = core.ScriptManager(ashita_dir, boot_name)
    manager.prepare()
    done["enabled"] = enable_shells_once(ashita_dir, manager, log=log)
    done["off"] = apply_choice(ashita_dir, manager, log=log)
    return done
