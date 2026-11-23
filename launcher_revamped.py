"""The Revamped switch: turn the old Squads / old Gambits off and the Revamped versions on, from one saved choice.

No Tk in here (the launcher's pages and dev\\launcher-tests\\test_revamped.py both use it).

THE FILE (agreed with the shell-add-on lane, the in-game prompt reads and writes the same one):
    <kit>\\config\\revamped.json        {"squads": "revamped" | "stock", "gambits": "revamped" | "stock", "asked": true}
  - a key that is missing means "no choice yet" (nothing is switched);
  - "asked" is set by the launcher's own question, so No is not asked again at every start;
  - the launcher also keeps "off": the autoload lines IT switched off, so "stock" puts back exactly those.
  Unknown keys are kept as they are.

THE SWITCH (what a choice does to Ashita\\scripts\\xiforever.txt, and dualbox.txt when it exists):
  "revamped": the Revamped add-on's "/addon load" line is on, and the old add-on's line is commented out in place:
              "# (off 2026-11-23, launcher Revamped switch) /addon load Gambits". Nothing is deleted: the old add-on's folder,
              its settings and the line itself stay, so "stock" is one click.
  "stock":    the Revamped line is commented out the same way and the old lines come back on (only lines the launcher or the
              Revamped installer commented out, never a line you wrote a '#' in front of yourself).
  It is run before every launch, so an Addons-tab toggle or a file update that brings an old line back is undone while the
  choice is "revamped". A Revamped add-on that is NOT installed is never switched on and the old ones are left alone: the
  switch can not leave you with neither.

WHAT COUNTS AS INSTALLED: Ashita\\addons\\GambitsRevamped\\GambitsRevamped.lua and Ashita\\addons\\SquadsRevamped\\SquadsRevamped.lua.
A folder holding a file called shell.json is a SHELL (the hook points our packages plug into, no Revamped features of its
own): the launcher stays silent about shells and does not switch anything for them."""
import json
import os
import re
import time

COMPONENTS = ("gambits", "squads")
# component -> the Revamped add-on, and the old add-ons whose autoload line is turned off for it. servermenu is NOT in the
# squads list on purpose: it also holds the server menu, and SquadsRevamped hides its Squads button by itself.
TABLE = {
    "gambits": {"addon": "GambitsRevamped", "old": ("Gambits", "autobox"), "label": "Gambits"},
    "squads": {"addon": "SquadsRevamped", "old": ("squadpanel",), "label": "Squads"},
}
SHELL_MARK = "shell.json"
TAG = "launcher Revamped switch"
SCRIPTS = ("xiforever.txt", "phoenixsolo.txt", "dualbox.txt")
QUESTION = "Turn off the old Squads / old Gambits and use the Revamped versions?"

_LOAD = r"/addon\s+load\s+%s"


def config_path(kit):
    return os.path.join(kit, "config", "revamped.json")


def load(kit):
    try:
        with open(config_path(kit), encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(kit, data):
    path = config_path(kit)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def choice(kit, component):
    value = load(kit).get(component)
    return value if value in ("revamped", "stock") else None


def state(ashita_dir, component):
    """'package' (installed, real), 'shell' (a shell.json is in it) or None."""
    name = TABLE[component]["addon"]
    folder = os.path.join(ashita_dir, "addons", name)
    if not os.path.isfile(os.path.join(folder, name + ".lua")):
        return None
    return "shell" if os.path.isfile(os.path.join(folder, SHELL_MARK)) else "package"


def detected(ashita_dir):
    """Components whose Revamped package (not a shell) is installed."""
    return [c for c in COMPONENTS if ashita_dir and state(ashita_dir, c) == "package"]


def to_ask(kit, ashita_dir):
    """The components the launcher should ask about now: a package is installed, nothing was chosen, never asked."""
    if load(kit).get("asked"):
        return []
    return [c for c in detected(ashita_dir) if choice(kit, c) is None]


def question(components):
    if len(components) == 1:
        return QUESTION.replace("old Squads / old Gambits", "old " + TABLE[components[0]]["label"]).replace("versions", "version")
    return QUESTION


def _scripts(ashita_dir):
    folder = os.path.join(ashita_dir, "scripts")
    return [os.path.join(folder, n) for n in SCRIPTS if os.path.isfile(os.path.join(folder, n))]


def _read(path):
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig")
    eol = "\r\n" if "\r\n" in text else "\n"
    return text.split(eol) if text else [], eol


def _write(path, lines, eol):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(eol.join(lines))


def _set_line(lines, name, on, date, owned):
    """Turns /addon load <name> on or off in place. Returns True when something changed. owned: commented-out forms that
    may be turned back on (ours, or the Revamped installer's '# (off DATE, ...) /addon load X')."""
    load = re.compile(r"^\s*" + _LOAD % re.escape(name) + r"\s*$", re.I)
    off = re.compile(r"^#\s*\(off [^)]*\)\s*" + _LOAD % re.escape(name) + r"\s*$", re.I)
    active = [i for i, l in enumerate(lines) if load.match(l)]
    dormant = [i for i, l in enumerate(lines) if off.match(l)]
    if on:
        if active:
            return False
        if dormant and owned:
            lines[dormant[0]] = "/addon load " + name
            return True
        if not dormant:
            return None                           # never in the script: the caller decides whether to add it
        return False
    changed = False
    for i in active:
        lines[i] = "# (off %s, %s) /addon load %s" % (date, TAG, name)
        changed = True
    return changed


def _after(lines, anchors, new):
    """Adds a line after the last of the anchor loads (else after the last '/addon load', else at the end)."""
    at = None
    for i, l in enumerate(lines):
        m = re.match(r"^\s*/addon\s+load\s+(\S+)\s*$", l, re.I)
        if m and (m.group(1).lower() in anchors):
            at = i
    if at is None:
        for i, l in enumerate(lines):
            if re.match(r"^\s*/addon\s+load\s+\S+\s*$", l, re.I):
                at = i
    if at is None:
        while lines and lines[-1] == "":
            lines.pop()
        lines.append(new)
        lines.append("")
    else:
        lines.insert(at + 1, new)


def apply(kit, ashita_dir, date=None):
    """Makes the start scripts follow revamped.json. Idempotent; returns what it did, one line per change."""
    if not ashita_dir or not os.path.isdir(ashita_dir):
        return []
    date = date or time.strftime("%Y-%m-%d")
    data = load(kit)
    offs = [x for x in data.get("off", []) if isinstance(x, str)]
    messages = []
    for component in COMPONENTS:
        want = data.get(component)
        if want not in ("revamped", "stock"):
            continue
        spec = TABLE[component]
        if state(ashita_dir, component) is None:
            continue                                 # not installed: never leave the player with neither
        for path in _scripts(ashita_dir):
            lines, eol = _read(path)
            script = os.path.basename(path)
            changed = []
            if want == "revamped":
                result = _set_line(lines, spec["addon"], True, date, True)
                if result is None:
                    _after(lines, [o.lower() for o in spec["old"]] + ["servermenu"], "/addon load " + spec["addon"])
                if result is None or result:
                    changed.append("on " + spec["addon"])
                for old in spec["old"]:
                    if _set_line(lines, old, False, date, False):
                        changed.append("off " + old)
                        key = script + ":" + old
                        if key not in offs:
                            offs.append(key)
            else:
                if _set_line(lines, spec["addon"], False, date, False):
                    changed.append("off " + spec["addon"])
                for old in spec["old"]:
                    key = script + ":" + old
                    if _set_line(lines, old, True, date, True):
                        changed.append("on " + old)
                    if key in offs:
                        offs.remove(key)
            if changed:
                _write(path, lines, eol)
                messages.append("%s: %s (%s)" % (script, ", ".join(changed), spec["label"]))
    if offs != [x for x in data.get("off", []) if isinstance(x, str)]:
        data["off"] = offs
        save(kit, data)
    return messages


def choose(kit, ashita_dir, components, value, date=None):
    """Saves the choice for these components ('revamped' or 'stock'), marks the question as asked, and applies it."""
    data = load(kit)
    for c in components:
        if c in COMPONENTS and value in ("revamped", "stock"):
            data[c] = value
    data["asked"] = True
    save(kit, data)
    return apply(kit, ashita_dir, date)


def declined(kit):
    """No: nothing is chosen or switched, and the question is not asked again."""
    data = load(kit)
    data["asked"] = True
    save(kit, data)
