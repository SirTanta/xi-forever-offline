"""era_launcher.py: read, write, compare and plan (the launcher side of docs/design/ERA_AND_EXPANSIONS.md 2.3 to 4). Stdlib only; one parity test needs lupa and
the era.lua of SirTanta/gambits-revamped (env ERA_LUA, else D:/wt/era-core/core/server/era.lua) and is skipped without them."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import era_launcher as EL  # noqa: E402

TIER_T1 = """-- zz_tiers.lua : written by tier.py (T1)
xi = xi or {}
xi.settings = xi.settings or {}
xi.settings.main  = xi.settings.main  or {}
xi.settings.login = xi.settings.login or {}

xi.settings.main.MAX_LEVEL = 75
xi.settings.main.ENABLE_WOTG = 1
xi.settings.main.ENABLE_ACP = 1
xi.settings.main.ENABLE_AMK = 1
xi.settings.main.ENABLE_ASA = 1
xi.settings.login.WINGS_OF_THE_GODDESS = true

print('[tiers] overlay loaded: T1, MAX_LEVEL=' .. tostring(xi.settings.main.MAX_LEVEL))
"""


@pytest.fixture
def dirs(tmp_path):
    s = tmp_path / "settings"
    r = tmp_path / "rates"
    s.mkdir()
    r.mkdir()
    return str(s), str(r)


def put(settings, step, **kw):
    plan = dict(step=step, reached=kw.pop("reached", step), lock=False, gates=True, gate_open=None, restart=0, mode="75")
    plan.update(kw)
    ok, res = EL.write_era(settings, plan)
    assert ok, res
    return res


# ---------------------------------------------------------------- read
def test_no_file_is_not_active(dirs):
    st = EL.read_state(dirs[0])
    assert st["active"] is False and st["src"] == "none"
    assert EL.shown(st).startswith("Era: not switched on")


def test_a_tier_tool_file_derives_the_step(dirs):
    with open(os.path.join(dirs[0], EL.FILE_NAME), "w") as f:
        f.write(TIER_T1)
    st = EL.read_state(dirs[0])
    assert st["step"] == "wotg" and st["src"] == "tier" and st["reached"] == "wotg"
    assert st["active"] is False  # the feature is not on: the file was not written by the era writer
    assert st["flags"]["wotg"] is True and st["flags"]["abyssea"] is False  # a flag the tier tool did not write is off


def test_the_written_file_reads_back(dirs):
    put(dirs[0], "wotg", reached="rov", lock=True, gate_open=None, restart=77, mode="retail")
    st = EL.read_state(dirs[0])
    assert (st["active"], st["step"], st["reached"], st["lock"], st["restart"], st["mode"], st["src"]) == (True, "wotg", "rov", True, 77, "retail", "era")
    assert st["cap"] == 75 and st["sync"] == 75
    assert st["flags"]["wotg"] and not st["flags"]["abyssea"]
    assert "(staying)" in EL.shown(st)


def test_shown_names_the_open_gate(dirs):
    put(dirs[0], "toau", gate_open="wotg")
    assert EL.shown(EL.read_state(dirs[0])) == "Era: Treasures of Aht Urhgan. Continue to Wings of the Goddess"
    put(dirs[0], "toau", gate_open="wotg", lock=True)
    assert "Continue" not in EL.shown(EL.read_state(dirs[0]))


# ---------------------------------------------------------------- write
def test_write_keeps_prev_and_every_flag_explicit(dirs):
    s = dirs[0]
    path = os.path.join(s, EL.FILE_NAME)
    with open(path, "w") as f:
        f.write(TIER_T1)
    put(s, "toau")
    text = open(path).read()
    for k in EL.FLAG_KEYS:
        assert "xi.settings.main.%s = " % EL.FLAG_NAME[k] in text
    assert "ENABLE_WOTG = 0" in text and "ENABLE_TOAU = 1" in text
    assert open(path + ".prev").read() == TIER_T1  # tier.py undo still works
    assert not os.path.exists(path + ".new")
    assert "[tiers] overlay loaded: T0, MAX_LEVEL=" in text
    assert EL.check_text(text) == []


@pytest.mark.parametrize("mode", ["inplace", "rename"])
def test_both_replace_modes(dirs, mode):
    ok, _ = EL.write_era(dirs[0], dict(step="wotg", reached="wotg", lock=False, gates=True, gate_open=None, restart=0, mode="75"), mode)
    assert ok and EL.read_state(dirs[0])["step"] == "wotg"
    assert not os.path.exists(os.path.join(dirs[0], EL.FILE_NAME + ".new"))


def test_step_back_writes_the_later_flags_off_and_keeps_reached(dirs):
    put(dirs[0], "rov")
    st = EL.read_state(dirs[0])
    EL.write_era(dirs[0], EL.plan_of(st, step="toau"))
    text = open(os.path.join(dirs[0], EL.FILE_NAME)).read()
    for k in ("WOTG", "ACP", "AMK", "ASA", "ABYSSEA", "VOIDWATCH", "SOA", "ROV", "TVR"):
        assert "xi.settings.main.ENABLE_%s = 0" % k in text
    st2 = EL.read_state(dirs[0])
    assert st2["step"] == "toau" and st2["reached"] == "rov" and st2["sync"] == 75
    assert "ERA_REACHED = \"rov\"" in text and "GEAR_SYNC_LEVEL = 75" in text


def test_unknown_step_in_a_plan_is_refused_by_the_checker():
    assert EL.check_text("xi.settings.main.X = = 1\n") != []
    assert EL.check_text("os.execute('x')\n") != []


def test_a_failed_check_never_touches_the_live_file(dirs, monkeypatch):
    s = dirs[0]
    path = os.path.join(s, EL.FILE_NAME)
    with open(path, "w") as f:
        f.write("-- keep me\n")
    monkeypatch.setattr(EL, "render", lambda plan: "xi.settings.main.X = = 1\n")
    ok, why = EL.write_era(s, dict(step="wotg"))
    assert ok is False and "does not check" in why
    assert open(path).read() == "-- keep me\n" and not os.path.exists(path + ".new")


# ---------------------------------------------------------------- applied record and compare
def test_applied_record_roundtrip(dirs):
    s, r = dirs
    put(s, "toau")
    data = EL.mark_applied(s, r)
    assert data["step"] == "toau" and data["flags"]["toau"] is True
    assert json.load(open(EL.applied_path(r)))["step"] == "toau"
    assert EL.load_applied(r)["reached"] == "toau"
    assert EL.load_applied(os.path.join(r, "nowhere")) == {}


def test_compare_nothing_when_equal_and_when_off(dirs):
    s, r = dirs
    assert EL.compare(EL.read_state(s), {})["layer2"] is False
    put(s, "toau")
    EL.mark_applied(s, r)
    c = EL.compare(EL.read_state(s), EL.load_applied(r))
    assert (c["layer2"], c["layer3"]) == (False, False)


def test_compare_after_continue_to_wotg(dirs):
    s, r = dirs
    put(s, "toau", gate_open="wotg")
    EL.mark_applied(s, r)
    res = EL.continue_to(s, r)
    assert res["ok"] and res["to"] == "wotg"
    st = EL.read_state(s)
    assert st["step"] == "wotg" and st["gate_open"] is None and st["reached"] == "wotg"
    c = EL.compare(st, EL.load_applied(r))
    assert c["layer2"] is True and c["layer3"] is True and (c["frm"], c["to"]) == ("toau", "wotg")
    assert c["plan2"]["init_remove"] == ["era/data/wotg", "era/sql/wotg"] and c["plan2"]["dbtool"] is True
    assert len(c["plan3"]["to_retail"]) == 16 and c["plan3"]["needs_game_closed"]
    EL.mark_applied(s, r)
    assert EL.compare(EL.read_state(s), EL.load_applied(r))["layer2"] is False


def test_continue_refuses_without_a_gate_or_when_locked_or_off(dirs):
    s, r = dirs
    assert EL.continue_to(s, r)["ok"] is False  # off
    put(s, "toau")
    assert "no gate" in EL.continue_to(s, r)["why"]
    put(s, "toau", gate_open="wotg", lock=True)
    assert "staying" in EL.continue_to(s, r)["why"]
    assert EL.read_state(s)["step"] == "toau"


def test_lock_and_unlock(dirs):
    s, r = dirs
    put(s, "toau", gate_open="wotg")
    assert EL.set_lock(s, True)["ok"]
    st = EL.read_state(s)
    assert st["lock"] and st["gate_open"] is None and st["sync"] == 75
    assert EL.set_lock(s, False)["ok"] and not EL.read_state(s)["lock"]
    assert EL.set_lock(dirs[0] + "x", True)["ok"] is False  # off in a folder with no file


# ---------------------------------------------------------------- plans
def test_layer_plans_forward_and_back():
    p2 = EL.plan_layer2("toau", "rov")
    assert set(p2["init_remove"]) == {"era/data/wotg", "era/sql/wotg", "era/sql/abyssea", "era/sql/soa", "era/sql/rov"}
    back = EL.plan_layer2("rov", "toau")
    assert set(back["init_add"]) == set(p2["init_remove"]) and back["init_remove"] == []
    assert EL.plan_layer2("cop", "cop")["restart_map"] is False
    p3 = EL.plan_layer3("toau", "voidwatch")
    assert len(p3["to_retail"]) == 16 + 3 + 13 and p3["to_era"] == []
    back3 = EL.plan_layer3("voidwatch", "wotg")
    assert len(back3["to_era"]) == 3 + 13 and back3["to_retail"] == []
    assert EL.plan_layer3("original", "toau") == {"to_retail": [], "to_era": [], "needs_game_closed": False}


def test_dat_groups_match_tier_py_t3():
    """the same 16 + 3 + 13 files as tier.py T3 (GP_SHOPS, ROM4/0/53 55 58, and the voidwatch list)"""
    assert EL.GP_SHOPS[0] == "ROM/21/39" and EL.GP_SHOPS[-1] == "ROM/25/50" and len(EL.GP_SHOPS) == 16
    assert EL.DATS["abyssea"] == ("ROM4/0/53", "ROM4/0/55", "ROM4/0/58")
    assert "ROM3/2/34" in EL.DATS["voidwatch"] and len(EL.DATS["voidwatch"]) == 13


# ---------------------------------------------------------------- the restart request
def test_restart_request_is_handled_once_and_only_when_supervising(dirs):
    s, r = dirs
    put(s, "toau")
    EL.mark_applied(s, r)
    assert EL.restart_requested(s, r, True) is False  # no request
    assert EL.request_restart(s, 1000)
    assert EL.restart_requested(s, r, False) is False  # servers started by hand: nothing happens
    assert EL.restart_requested(s, r, True) is True
    assert EL.restart_requested(s, r, True) is False  # handled
    assert EL.request_restart(s, 2000) and EL.restart_requested(s, r, True) is True


def test_restart_request_needs_the_era_on(dirs):
    s, r = dirs
    assert EL.request_restart(s, 5) is False and EL.restart_requested(s, r, True) is False


# ---------------------------------------------------------------- parity with era.lua
def test_render_equals_era_lua_for_every_step(tmp_path):
    lupa = pytest.importorskip("lupa.luajit21")
    era = os.environ.get("ERA_LUA") or "D:/wt/era-core/core/server/era.lua"
    if not os.path.exists(era):
        pytest.skip("era.lua of gambits-revamped is not on this PC")
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("xi = { settings = { main = {}, login = {} } }")
    rt.execute("local f = assert(loadfile('%s')) f()" % era.replace("\\", "/"))
    for step in EL.ORDER:
        for mode in ("75", "retail"):
            for reached, lock in ((step, False), ("rov", False), (step, True)):
                plan = dict(step=step, reached=reached, lock=lock, gates=True, gate_open="wotg" if step == "toau" else None, restart=5, mode=mode)
                lua = rt.eval(
                    "(ERA.render({ step = '%s', reached = '%s', lock = %s, gates = true, gateOpen = %s, restart = 5, mode = '%s' }))"
                    % (step, reached, "true" if lock else "false", "'wotg'" if step == "toau" else "nil", mode)
                )
                assert EL.render(plan) == lua, (step, mode, reached, lock)
