"""era_launcher.py: the launcher side of "Era and expansions" (docs/design/ERA_AND_EXPANSIONS.md, sections 2.3, 3 and 4).

A standalone module (stdlib only, no GUI, no game, no server). The v0.3.3 launcher sources are not tracked in this repo yet, so the GUI wiring waits; the
logic lives here so it can be tested now and imported by the launcher later.

What it does
  read_era(settings_dir)          the era fields of server/settings/zz_tiers.lua (the one file era.lua, tier.py and the launcher all read)
  state_of(fields)                the world as the file says it: active, step, reached, lock, gate open, restart, cap, flags, who wrote it
  render(plan) / write_era(...)   the same text and the same safe write as core/server/era.lua (every flag on AND off, .prev, .new checked, then replace)
  load_applied / save_applied     era_applied.json next to rates.json: what the launcher last applied
  compare(state, applied)         what differs: layer 2 (init.txt, dbtool) and layer 3 (client files)
  plan_layer2(frm, to) / plan_layer3(frm, to)   the work to do for one move
  continue_to(...)                the Continue button of the home page: the same writer rules, then the plan
  request_restart / restart_requested   ERA_RESTART: "Restart the world now"

Nothing here runs unless the era feature is on (ERA_GATES = 1 in the file). With the feature off every function answers "nothing to do" and writes nothing.
"""

import json
import os
import re

ORDER = ("original", "zilart", "cop", "toau", "wotg", "abyssea", "voidwatch", "soa", "rov")

FLAG_KEYS = ("rotz", "cop", "toau", "wotg", "acp", "amk", "asa", "abyssea", "voidwatch", "soa", "rov", "tvr")
FLAG_NAME = {k: "ENABLE_" + k.upper() for k in FLAG_KEYS}
LOGIN_NAMES = ("WINGS_OF_THE_GODDESS", "VISIONS_OF_ABYSSEA", "SCARS_OF_ABYSSEA", "HEROES_OF_ABYSSEA", "SEEKERS_OF_ADOULIN")

# name, flags it turns on, login flags, cap in 75 mode, cap in retail mode, the tier.py name (None = none)
STEPS = {
    "original": dict(name="Original", flags=(), login=(), cap=75, cap_retail=75, tier=None),
    "zilart": dict(name="Rise of the Zilart", flags=("rotz",), login=(), cap=75, cap_retail=75, tier=None),
    "cop": dict(name="Chains of Promathia", flags=("cop",), login=(), cap=75, cap_retail=75, tier=None),
    "toau": dict(name="Treasures of Aht Urhgan", flags=("toau",), login=(), cap=75, cap_retail=75, tier="T0"),
    "wotg": dict(name="Wings of the Goddess", flags=("wotg", "acp", "amk", "asa"), login=("WINGS_OF_THE_GODDESS",), cap=75, cap_retail=75, tier="T1"),
    "abyssea": dict(
        name="Abyssea", flags=("abyssea",), login=("VISIONS_OF_ABYSSEA", "SCARS_OF_ABYSSEA", "HEROES_OF_ABYSSEA"), cap=75, cap_retail=90, tier="T2"
    ),
    "voidwatch": dict(name="Voidwatch and level 99", flags=("voidwatch",), login=(), cap=75, cap_retail=99, tier="T3"),
    "soa": dict(name="Seekers of Adoulin", flags=("soa",), login=("SEEKERS_OF_ADOULIN",), cap=75, cap_retail=99, tier="T4"),
    "rov": dict(name="Rhapsodies of Vana'diel", flags=("rov", "tvr"), login=(), cap=75, cap_retail=99, tier="T5"),
}

# client files (ROM/<dir>/<n> without .DAT) that an expansion makes obsolete: retail from the step on, the era file before (from tier.py, same lists)
GP_SHOPS = tuple("ROM/%d/%d" % (d, n) for d in (21, 25) for n in (39, 40, 43, 44, 46, 47, 49, 50))
DATS = {
    "wotg": GP_SHOPS,
    "abyssea": ("ROM4/0/53", "ROM4/0/55", "ROM4/0/58"),
    "voidwatch": (
        "ROM/20/*", "ROM/24/*", "ROM2/13/5", "ROM2/13/6", "ROM2/13/7", "ROM2/13/8", "ROM2/13/9", "ROM2/13/10", "ROM2/17/*",
        "ROM3/0/77", "ROM3/0/90", "ROM3/2/21", "ROM3/2/34",
    ),
}

# modules/init.txt: a line that is listed while its expansion is OFF (it reverts that expansion's era data, or applies its era sql)
INIT_WHILE_OFF = {
    "wotg": ("era/data/wotg", "era/sql/wotg"),
    "toau": ("era/sql/toau",),
    "abyssea": ("era/sql/abyssea",),
    "soa": ("era/sql/soa",),
    "rov": ("era/sql/rov",),
}

BASELINE_ON = ("rotz", "cop", "toau")
FILE_NAME = "zz_tiers.lua"
APPLIED_NAME = "era_applied.json"


def index_of(step):
    return ORDER.index(step)


def flags_of(step):
    """every flag of every earlier step on, the rest off"""
    on = set()
    for s in ORDER[: index_of(step) + 1]:
        on.update(STEPS[s]["flags"])
    return {k: (k in on) for k in FLAG_KEYS}


def cap_of(step, mode):
    return STEPS[step]["cap_retail" if mode == "retail" else "cap"]


# ---------------------------------------------------------------- read
_ASSIGN = re.compile(r"^\s*xi\.settings\.(main|login)\.([A-Za-z0-9_]+)\s*=\s*(.+?)\s*$")


def _value(text):
    text = text.strip()
    if text in ("true", "false"):
        return text == "true"
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def read_era(settings_dir):
    """{'main': {KEY: value}, 'login': {...}} from zz_tiers.lua; empty when the file is missing. Only plain assignments are read."""
    out = {"main": {}, "login": {}}
    path = os.path.join(settings_dir, FILE_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = _ASSIGN.match(line)
                if m:
                    out[m.group(1)][m.group(2)] = _value(m.group(3))
    except OSError:
        pass
    return out


def state_of(fields):
    """The world as the file says it. src: 'none' (no file), 'tier' (written by tier.py, no era fields), 'era'."""
    main = fields.get("main", {})
    # a flag the file does not write keeps what the launcher's own main.lua gives it: Zilart, CoP and ToAU on, everything after them off
    # (tier.py only ever writes the flags it turns on)
    flags = {k: int(main.get(FLAG_NAME[k], 1 if k in BASELINE_ON else 0)) == 1 for k in FLAG_KEYS}
    derived = ORDER[0]
    for s in ORDER:
        if all(flags[k] for k in STEPS[s]["flags"]):
            derived = s
        else:
            break
    named = main.get("ERA_NAME") if main.get("ERA_NAME") in ORDER else None
    step = named or derived
    reached = main.get("ERA_REACHED") if main.get("ERA_REACHED") in ORDER else step
    if index_of(reached) < index_of(step):
        reached = step
    mode = "retail" if main.get("ERA_CAP_MODE") == "retail" else "75"
    gate = main.get("ERA_GATE_OPEN")
    return {
        "active": int(main.get("ERA_GATES", 0) or 0) == 1,
        "step": step,
        "derived": derived,
        "reached": reached,
        "lock": int(main.get("ERA_LOCK", 0) or 0) == 1,
        "gate_open": gate if gate in ORDER else None,
        "restart": int(main.get("ERA_RESTART", 0) or 0),
        "mode": mode,
        "cap": cap_of(step, mode),
        "flags": flags,
        "sync": int(main.get("GEAR_SYNC_LEVEL", 0) or 0),
        "src": "era" if named else ("tier" if main else "none"),
    }


def read_state(settings_dir):
    return state_of(read_era(settings_dir))


def shown(state):
    """what the Era page and the home page say (the live file, never rates.json)"""
    if not state["active"]:
        return "Era: not switched on (the server settings decide)"
    words = "Era: %s%s" % (STEPS[state["step"]]["name"], " (staying)" if state["lock"] else "")
    if state["src"] == "tier":
        words += " (written by the tier tool)"
    if state["gate_open"] and not state["lock"]:
        words += ". Continue to %s" % STEPS[state["gate_open"]]["name"]
    return words


# ---------------------------------------------------------------- write (the same text as era.lua)
def render(plan):
    """plan: step, reached, lock, gates, gate_open, restart, mode. Byte for byte what core/server/era.lua E.render writes."""
    step = plan["step"]
    info = STEPS[step]
    at = index_of(step)
    flags = flags_of(step)
    mode = plan.get("mode", "75")
    cap = cap_of(step, mode)
    reached = plan.get("reached", step)
    gates = bool(plan.get("gates", True))
    lock = bool(plan.get("lock", False))
    login = set()
    for s in ORDER[: at + 1]:
        login.update(STEPS[s]["login"])
    sync = cap if gates and (lock or index_of(reached) > at) else 0
    any_off = not all(flags.values())
    lines = [
        "-----------------------------------",
        "-- zz_tiers.lua : written by era.lua (%s: %s)" % (step, info["name"]),
        "-- Every flag is written, on and off, so a step back turns the later ones off. tier.py reads and writes the same file.",
        "-----------------------------------",
        "xi = xi or {}",
        "xi.settings = xi.settings or {}",
        "xi.settings.main  = xi.settings.main  or {}",
        "xi.settings.login = xi.settings.login or {}",
        "",
        "xi.settings.main.MAX_LEVEL = %d" % cap,
    ]
    for k in FLAG_KEYS:
        lines.append("xi.settings.main.%s = %d" % (FLAG_NAME[k], 1 if flags[k] else 0))
    lines.append("xi.settings.main.RESTRICT_CONTENT = %d" % (1 if gates and any_off else 0))
    for n in LOGIN_NAMES:
        lines.append("xi.settings.login.%s = %s" % (n, "true" if n in login else "false"))
    lines.append('xi.settings.main.ERA_NAME = "%s"' % step)
    lines.append('xi.settings.main.ERA_REACHED = "%s"' % reached)
    lines.append("xi.settings.main.ERA_LOCK = %d" % (1 if lock else 0))
    lines.append("xi.settings.main.ERA_GATES = %d" % (1 if gates else 0))
    lines.append('xi.settings.main.ERA_GATE_OPEN = "%s"' % (plan.get("gate_open") or ""))
    lines.append("xi.settings.main.ERA_RESTART = %d" % int(plan.get("restart", 0) or 0))
    lines.append('xi.settings.main.ERA_CAP_MODE = "%s"' % mode)
    lines.append("xi.settings.main.GEAR_SYNC_LEVEL = %d" % sync)
    lines.append("")
    lines.append("print('[tiers] overlay loaded: %s, MAX_LEVEL=' .. tostring(xi.settings.main.MAX_LEVEL))" % (info["tier"] or step))
    lines.append("")
    return "\n".join(lines)


_OK_LINE = re.compile(
    r"^(\s*--.*|xi = xi or \{\}|xi\.settings = xi\.settings or \{\}|xi\.settings\.(main|login)\s*= xi\.settings\.(main|login)\s+or \{\}"
    r"|xi\.settings\.(main|login)\.[A-Za-z0-9_]+ = (-?\d+|true|false|\"[A-Za-z0-9_]*\")"
    r"|print\('\[tiers\] overlay loaded: [A-Za-z0-9_]+, MAX_LEVEL=' \.\. tostring\(xi\.settings\.main\.MAX_LEVEL\)\)|)$"
)


def check_text(text):
    """A stand-in for the server's `loadfile` check (there is no Lua in the launcher): every line must be a line the writer makes. Returns problems."""
    return ["line %d: %r" % (i, ln) for i, ln in enumerate(text.split("\n"), 1) if not _OK_LINE.match(ln)]


def write_era(settings_dir, plan, replace_mode="inplace"):
    """.prev backup, .new checked, then the live file. Returns (True, path) or (False, reason). Never leaves a half-written live file."""
    text = render(plan)
    problems = check_text(text)
    if problems:
        return False, "the new file does not check: " + problems[0]
    path = os.path.join(settings_dir, FILE_NAME)
    os.makedirs(settings_dir, exist_ok=True)
    try:
        if os.path.exists(path):
            with open(path, "rb") as f, open(path + ".prev", "wb") as g:
                g.write(f.read())
        with open(path + ".new", "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if replace_mode == "rename":
            os.replace(path + ".new", path)  # atomic on Windows too (os.replace overwrites)
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.remove(path + ".new")
    except OSError as e:
        return False, "could not write: %s" % e
    return True, path


def plan_of(state, **over):
    p = dict(step=state["step"], reached=state["reached"], lock=state["lock"], gates=True, gate_open=state["gate_open"], restart=state["restart"], mode=state["mode"])
    p.update(over)
    return p


# ---------------------------------------------------------------- what the launcher last applied
def applied_path(rates_dir):
    return os.path.join(rates_dir, APPLIED_NAME)


def load_applied(rates_dir):
    try:
        with open(applied_path(rates_dir), encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_applied(rates_dir, state, restart_handled=None):
    data = {
        "step": state["step"],
        "reached": state["reached"],
        "cap_mode": state["mode"],
        "flags": {k: v for k, v in state["flags"].items()},
        "restart_handled": state["restart"] if restart_handled is None else restart_handled,
    }
    os.makedirs(rates_dir, exist_ok=True)
    tmp = applied_path(rates_dir) + ".new"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, applied_path(rates_dir))
    return data


# ---------------------------------------------------------------- the plan for layers 2 and 3
def plan_layer2(frm, to):
    """modules/init.txt and dbtool. A line is listed while its expansion is off: turning an expansion on removes its lines, turning it off adds them."""
    before, after = flags_of(frm), flags_of(to)
    remove, add = [], []
    for exp, lines in INIT_WHILE_OFF.items():
        was_off, now_off = not before[exp], not after[exp]
        if was_off and not now_off:
            remove.extend(lines)
        elif not was_off and now_off:
            add.extend(lines)
    sql_changed = [ln for ln in remove + add if ln.startswith("era/sql/")]
    return {"init_remove": remove, "init_add": add, "dbtool": bool(sql_changed), "restart_map": bool(remove or add)}


def plan_layer3(frm, to):
    """client files: the retail pair of every group the step turns on, the era file of every group it turns off. The game must be closed."""
    a, b = index_of(frm), index_of(to)
    to_retail, to_era = [], []
    for group, files in DATS.items():
        gi = index_of(group)
        if a < gi <= b:
            to_retail.extend(files)
        elif b < gi <= a:
            to_era.extend(files)
    return {"to_retail": to_retail, "to_era": to_era, "needs_game_closed": bool(to_retail or to_era)}


def compare(state, applied):
    """What differs between the file and what the launcher applied last (layer 2: init.txt and dbtool, layer 3: client files).
    With no record the launcher never applied an era: the world is taken as it stands (the step the flags show), so nothing is planned until the first move."""
    none = {"layer2": False, "layer3": False, "frm": None, "to": None, "plan2": None, "plan3": None}
    if not state["active"]:
        return none
    frm = applied.get("step") if applied.get("step") in ORDER else state["derived"]
    to = state["step"]
    p2, p3 = plan_layer2(frm, to), plan_layer3(frm, to)
    return {"layer2": p2["restart_map"], "layer3": p3["needs_game_closed"], "frm": frm, "to": to, "plan2": p2, "plan3": p3}


# ---------------------------------------------------------------- the buttons
def continue_to(settings_dir, rates_dir, replace_mode="inplace"):
    """The home page "Continue to <next>": needs ERA_GATE_OPEN in the file. Writes the file with the same rules as era.lua, then returns the plan."""
    st = read_state(settings_dir)
    if not st["active"]:
        return {"ok": False, "why": "the era is not switched on"}
    if st["lock"]:
        return {"ok": False, "why": "this world is staying in its era (Unlock first)"}
    nxt = st["gate_open"]
    if nxt is None:
        return {"ok": False, "why": "no gate is open"}
    reached = nxt if index_of(nxt) > index_of(st["reached"]) else st["reached"]
    ok, res = write_era(settings_dir, plan_of(st, step=nxt, reached=reached, gate_open=None), replace_mode)
    if not ok:
        return {"ok": False, "why": res}
    return {"ok": True, "to": nxt, "applied": load_applied(rates_dir), "plan2": plan_layer2(st["step"], nxt), "plan3": plan_layer3(st["step"], nxt)}


def set_lock(settings_dir, lock, replace_mode="inplace"):
    st = read_state(settings_dir)
    if not st["active"]:
        return {"ok": False, "why": "the era is not switched on"}
    ok, res = write_era(settings_dir, plan_of(st, lock=bool(lock), gate_open=None if lock else st["gate_open"]), replace_mode)
    return {"ok": ok, "why": None if ok else res}


def request_restart(settings_dir, now, replace_mode="inplace"):
    """"Restart the world now" (the server writes the same field): the launcher, while it supervises the servers, sees the new value and restarts."""
    st = read_state(settings_dir)
    if not st["active"]:
        return False
    ok, _ = write_era(settings_dir, plan_of(st, restart=int(now)), replace_mode)
    return ok


def restart_requested(settings_dir, rates_dir, supervising):
    """True when ERA_RESTART is newer than the last one handled and the launcher supervises the servers. Not supervising: False (the message falls
    back to "on the next start"). Marks the request handled when it returns True."""
    st = read_state(settings_dir)
    if not st["active"] or st["restart"] <= 0:
        return False
    applied = load_applied(rates_dir)
    if st["restart"] <= int(applied.get("restart_handled", 0) or 0):
        return False
    if not supervising:
        return False
    data = dict(applied) if applied else {}
    if not data:
        data = save_applied(rates_dir, st, restart_handled=st["restart"])
    data["restart_handled"] = st["restart"]
    os.makedirs(rates_dir, exist_ok=True)
    with open(applied_path(rates_dir), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    return True


def mark_applied(settings_dir, rates_dir):
    """After layers 2 and 3 ran on PLAY: remember the step they were applied for."""
    return save_applied(rates_dir, read_state(settings_dir), restart_handled=load_applied(rates_dir).get("restart_handled"))
