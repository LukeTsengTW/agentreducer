import pytest

from agentreducer.cli.parser import InformationalExit, ParseFailure, parse_cli


@pytest.mark.parametrize("argv", [[], ["wrong_PRIVATE"], ["--format", "PRIVATE"],
    ["--for", "json"], ["--format"], ["prompt", "--oracle", "PRIVATE"],
    ["--format", "json", "prompt", "--credential=PRIVATE_PARSE_CANARY"],
    ["prompt", "--text", "PRIVATE", "--source"],
    ["prompt", "--text", "PRIVATE", "--verify-argv-json", "PRIVATE", "--unknown"]])
def test_invalid_tokens_never_print_or_raise_systemexit(argv, capsys):
    with pytest.raises(ParseFailure):
        parse_cli(argv)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("argv", [["--help"], ["--version"], ["prompt", "--help"],
    ["instructions", "--help"], ["repository", "--help"]])
def test_information_exits_zero(argv, capsys):
    with pytest.raises(InformationalExit) as got:
        parse_cli(argv)
    assert got.value.code == 0
    out = capsys.readouterr()
    assert out.out and not out.err
    if argv == ["--version"]:
        assert out.out == "agentreducer 0.1.0\n"


def test_parse_error_keeps_only_safe_already_parsed_context():
    with pytest.raises(ParseFailure) as got:
        parse_cli(["--format", "json", "prompt", "--oracle", "PRIVATE"])
    assert got.value.format == "json"
    assert got.value.domain == "prompt"
    assert "PRIVATE" not in str(got.value)


def test_domain_and_explicit_values_are_preserved():
    ns = parse_cli(["--format", "json", "prompt", "--source", "x", "--oracle", "command",
        "--text", "", "--candidate-path", "input.txt", "--verify-argv-json", '["p", ""]'])
    assert ns.text == "" and ns.format == "json" and ns.split == "paragraph"


def test_help_explains_consent_context_repetition_and_backend_bound(capsys):
    with pytest.raises(InformationalExit): parse_cli(['prompt','--help'])
    help_text=' '.join(capsys.readouterr().out.split())
    for phrase in ['Root options precede','uncontrolled and unfrozen','multiple agent',
                   'per-classification','backend model requests']:
        assert phrase in help_text
