from capability_runtime.discovery.loop import compile_capability


def test_compiler_parameterizes_secrets_and_drops_transcript():
    cap = compile_capability(
        goal="look up balance",
        start_url="https://parabank.parasoft.com/parabank/index.htm",
        params={"username": "john", "password": "demo"},
        trace=[
            {"type": "navigate", "url": "https://parabank.parasoft.com/parabank/index.htm", "control": None},
            {
                "type": "type",
                "control": {"role": "textbox", "name": "username", "input_name": "username", "id": None},
                "value": "john",
            },
            {
                "type": "type",
                "control": {"role": "textbox", "name": "password", "input_name": "password", "id": None},
                "value": "demo",
            },
            {
                "type": "click",
                "control": {"role": "button", "name": "Log In", "input_name": None, "id": None},
            },
            {
                "type": "click",
                "control": {"role": "link", "name": "13344", "input_name": None, "id": None},
            },
        ],
        outputs={"account_id": "12345", "available_balance": "$100.00"},
    )
    dumped = cap.model_dump_json()
    assert "demo" not in dumped
    assert "${password}" in dumped
    assert "${username}" in dumped
    assert "thought" not in dumped
    assert cap.steps[0].type == "navigate"
    assert any(s.type == "extract" for s in cap.steps)
    account_click = next(s for s in cap.steps if s.type == "click" and s.description and "first account" in s.description)
    assert account_click.target is not None
    assert any(loc.value == "#accountTable tbody tr td a" for loc in account_click.target.locators)
    assert "13344" not in dumped
    import re
    assert re.search(cap.checkpoint.url_regex or "", "https://parabank.parasoft.com/parabank/activity.htm?id=1")
