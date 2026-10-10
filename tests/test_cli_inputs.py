from fractions import Fraction
import pytest

from agentreducer.cli.inputs import (ConfigFailure, read_argv, read_text_input,
    positive_int, finite_seconds, exact_fraction, exit_code)


@pytest.mark.parametrize("value", ['{}', '"echo hi"', '[]', '[""]', '[1]',
    '["x", 1]', '["x", "\\u0000"]', 'not-json', '[null]'])
def test_argv_rejects_non_arrays_empty_executable_and_nul(value):
    with pytest.raises(ConfigFailure):
        read_argv(json_text=value)


def test_argv_preserves_empty_later_argument_and_spaces():
    assert read_argv(json_text='["C:\\\\Program Files\\\\p.exe", "-flag", "", "字"]') == (
        "C:\\Program Files\\p.exe", "-flag", "", "字")


def test_input_reads_utf8_once_and_empty_is_valid(tmp_path):
    calls = []
    assert read_text_input(use_stdin=True, read_stdin=lambda: calls.append(1) or '字'.encode()) == '字'
    assert calls == [1]
    assert read_text_input(text="") == ""
    p = tmp_path / 'private.txt'; p.write_bytes(b'\xff')
    with pytest.raises(ConfigFailure) as got:
        read_text_input(file_path=p)
    assert 'private' not in str(got.value)
    with pytest.raises(ConfigFailure):
        read_text_input(text='\ud800')


@pytest.mark.parametrize("value", [True, "true", "0", "-1", "1.5", "NaN", "inf"])
def test_positive_integer_is_strict(value):
    with pytest.raises(ConfigFailure): positive_int(value)


@pytest.mark.parametrize("value", [True, "0", "-1", "NaN", "inf", "-inf"])
def test_timeout_is_finite_positive(value):
    with pytest.raises(ConfigFailure): finite_seconds(value)


def test_exact_fraction_and_signed_exit_codes():
    assert exact_fraction('0.80') == Fraction(4, 5)
    assert exact_fraction('1/3') == Fraction(1, 3)
    assert exit_code('-2') == -2
    for bad in ['0', '-0.1', '1.01', 'NaN']:
        with pytest.raises(ConfigFailure): exact_fraction(bad)


def test_argv_file_is_strict_utf8_and_exclusive(tmp_path):
    p = tmp_path / 'argv with spaces.json'; p.write_text('["p", ""]', encoding='utf-8')
    assert read_argv(file_path=p) == ('p', '')
    for kwargs in [{}, {'file_path':p, 'json_text':'["p"]'}, {'file_path':tmp_path/'missing'}]:
        with pytest.raises(ConfigFailure): read_argv(**kwargs)
