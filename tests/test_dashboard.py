from pathlib import Path

from streamlit.testing.v1 import AppTest

from credit_score import monitoring

APP = (
    Path(__file__).resolve().parents[1] / "services" / "dashboard" / "src" / "dashboard" / "app.py"
)


def test_dashboard_renders_every_tab(frames_factory, tmp_path, monkeypatch):
    monitoring.save_directory(frames_factory(n=400), tmp_path)
    monkeypatch.setenv("DASHBOARD_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(str(APP), default_timeout=120).run()
    assert not app.exception
    assert [t.label for t in app.tabs][:4] == [
        "Stability",
        "Performance",
        "Policy and operation",
        "Vintages",
    ]
    app.select_slider[0].set_value("2017-06").run()  # nothing matured yet
    assert not app.exception
    assert any("No cohort has matured" in i.value for i in app.info)
