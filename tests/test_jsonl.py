from vamos.jsonl import append_lines


def test_lines_are_appended_one_per_line_creating_the_folder(tmp_path):
    path = tmp_path / "games" / "g.jsonl"
    append_lines(path, ['{"a": 1}'])
    append_lines(path, ['{"b": 2}', '{"c": 3}'])
    assert path.read_text() == '{"a": 1}\n{"b": 2}\n{"c": 3}\n'


def test_new_lines_start_after_a_half_written_last_line(tmp_path):
    path = tmp_path / "g.jsonl"
    path.write_text('{"a": 1}\n{"b": ')  # a crash cut the last line short
    append_lines(path, ['{"c": 3}'])
    assert path.read_text().splitlines() == ['{"a": 1}', '{"b": ', '{"c": 3}']


def test_nothing_to_append_leaves_no_file(tmp_path):
    path = tmp_path / "g.jsonl"
    append_lines(path, [])
    assert not path.exists()
    path.write_text("")
    append_lines(path, ['{"a": 1}'])
    assert path.read_text() == '{"a": 1}\n'
