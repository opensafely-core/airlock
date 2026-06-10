from assets.fix_slippers_tags import TAG_RE, fix_file, fix_tag, main


def transform(text):
    return TAG_RE.sub(fix_tag, text)


def test_attrs_with_hyphenated_bare_names():
    assert transform("{% attrs data-foo data-bar %}") == "{% attrs data_foo data_bar %}"


def test_attrs_mixes_bare_and_kwarg_names():
    text = '{% attrs href hx-get hx-target type=type|default:"button" %}'
    expected = '{% attrs href hx_get hx_target type=type|default:"button" %}'
    assert transform(text) == expected


def test_component_tag_with_hyphenated_kwarg():
    assert transform("{% #button data-modal=id %}") == "{% #button data_modal=id %}"


def test_any_component_tag_is_rewritten_not_just_button():
    text = '{% #table_header data-type="date" data-format="DD MMM YYYY" %}'
    expected = '{% #table_header data_type="date" data_format="DD MMM YYYY" %}'
    assert transform(text) == expected


def test_hyphens_in_quoted_value_are_preserved():
    text = '{% #button data-table-pagination="previous-page" %}'
    expected = '{% #button data_table_pagination="previous-page" %}'
    assert transform(text) == expected


def test_quoted_value_with_internal_spaces_is_preserved():
    text = '{% #button disabled=True class="hover:scale-110 transition-transform" %}'
    assert transform(text) == text


def test_argless_tag_is_left_alone():
    assert transform("{% #breadcrumbs %}") == "{% #breadcrumbs %}"


def test_non_slippers_tags_are_left_alone():
    text = "{% url 'job-list' as foo %} {% if x %} {% endif %}"
    assert transform(text) == text


def test_hyphenated_html_attributes_outside_template_tags_are_preserved():
    text = '<select data-multiselect data-placeholder="x" {% attrs multiple data-max-items %}>'
    expected = '<select data-multiselect data-placeholder="x" {% attrs multiple data_max_items %}>'
    assert transform(text) == expected


def test_fix_file_writes_and_returns_true_when_changed(tmp_path):
    f = tmp_path / "x.html"
    f.write_text("{% attrs data-foo %}")
    assert fix_file(f) is True
    assert f.read_text() == "{% attrs data_foo %}"


def test_fix_file_returns_false_and_does_not_rewrite_when_unchanged(tmp_path):
    f = tmp_path / "x.html"
    original = "{% attrs href %}"
    f.write_text(original)
    assert fix_file(f) is False
    assert f.read_text() == original


def test_fix_file_is_idempotent(tmp_path):
    f = tmp_path / "x.html"
    f.write_text("{% attrs data-foo %}\n{% #button data-bar=baz %}")
    assert fix_file(f) is True
    once = f.read_text()
    assert fix_file(f) is False
    assert f.read_text() == once


def test_main_recurses_into_subdirs_and_skips_non_html(tmp_path, capsys):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.html").write_text("{% attrs data-foo %}")
    (tmp_path / "sub" / "b.html").write_text("{% attrs data-bar %}")
    (tmp_path / "c.txt").write_text("{% attrs data-baz %}")
    main(str(tmp_path))
    assert (tmp_path / "a.html").read_text() == "{% attrs data_foo %}"
    assert (tmp_path / "sub" / "b.html").read_text() == "{% attrs data_bar %}"
    assert (tmp_path / "c.txt").read_text() == "{% attrs data-baz %}"
    out = capsys.readouterr().out
    assert "a.html" in out
    assert "b.html" in out
    assert "c.txt" not in out
