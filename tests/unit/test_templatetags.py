from airlock.templatetags.airlocktags import as_csv_data


def test_file_content_as_csv_data():
    content = "Header1,Header2,Header3\n" "One,Two,Three\n" "Four,Five,Six\n"
    assert as_csv_data(content) == {
        "headers": ["Header1", "Header2", "Header3"],
        "rows": [["One", "Two", "Three"], ["Four", "Five", "Six"]],
    }
