"""读到プロデューサーID 时信息库默认切到这个账号（hiro.cli.cmd_info.import_inventory）。"""


def test_import_account_switches(run_cli, hiro_home):
    """读到的首页数值带プロデューサーID：信息库切到这个账号（默认切换），这份数值记在它名下。"""
    from hiro.cli import cmd_info
    from hiro.cli.store import open_store

    cmd_info.import_inventory("supports", {"cards": {}, "complete": True}, "t")  # 账号未知时扫的
    out = cmd_info.import_inventory(
        "account", {"plv": 65, "ap": 100, "ap_max": 100, "jewel": 15720, "producer_id": "VRRL6GVW", "name": "式部"}, "t"
    )
    assert out.get("account") == "VRRL6GVW"
    st = open_store()
    assert st.current_account() == "VRRL6GVW"
    _meta, rows = st.latest("account")
    assert rows[0]["producer_id"] == "VRRL6GVW" and rows[0]["name"] == "式部" and rows[0]["plv"] == 65
    _, env = run_cli("info", "summary")
    assert env["data"]["account"]["producer_id"] == "VRRL6GVW"
    assert env["data"]["kinds"]["supports"]["count"] == 0  # 账号未知时的那份不算
