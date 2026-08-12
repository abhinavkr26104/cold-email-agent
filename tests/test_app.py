from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_pdf_uploader_appears_as_soon_as_input_mode_changes():
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(app_path).run(timeout=20)

    assert len(app.exception) == 0
    assert len(app.get("file_uploader")) == 0
    assert len(app.text_area) == 2

    app.radio[0].set_value("Upload PDF")
    app.run(timeout=20)

    assert len(app.exception) == 0
    assert len(app.get("file_uploader")) == 1
    assert app.get("file_uploader")[0].label == "Candidate profile PDF"
    assert len(app.text_area) == 1
