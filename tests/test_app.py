from pathlib import Path

from streamlit.testing.v1 import AppTest
import pytest


def test_pdf_uploader_appears_as_soon_as_input_mode_changes():
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(app_path).run(timeout=20)

    app.selectbox[0].set_value("Manual email")
    app.run(timeout=20)

    assert len(app.exception) == 0
    assert len(app.get("file_uploader")) == 0
    assert len(app.text_area) == 2

    app.radio[0].set_value("Upload PDF")
    app.run(timeout=20)

    assert len(app.exception) == 0
    assert len(app.get("file_uploader")) == 1
    assert app.get("file_uploader")[0].label == "Candidate profile PDF"
    assert len(app.text_area) == 1


@pytest.mark.parametrize(
    "page",
    ["Dashboard", "Profile & companies", "Matches", "Approval queue", "Outreach & replies", "Manual email", "Settings"],
)
def test_automation_pages_render_without_external_calls(page):
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(app_path).run(timeout=20)

    app.selectbox[0].set_value(page)
    app.run(timeout=20)

    assert len(app.exception) == 0


@pytest.mark.parametrize(
    ("button_label", "destination"),
    [
        ("Set up profile", "Profile & companies"),
        ("Find roles", "Matches"),
        ("Review queue", "Approval queue"),
        ("View replies", "Outreach & replies"),
    ],
)
def test_dashboard_workflow_buttons_navigate(button_label, destination):
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(app_path).run(timeout=20)

    next(button for button in app.button if button.label == button_label).click()
    app.run(timeout=20)

    assert len(app.exception) == 0
    navigation = next(selectbox for selectbox in app.selectbox if selectbox.label == "Navigation")
    assert navigation.value == destination
