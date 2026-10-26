"""Isolated browser QA server. Uses a fake provider and temporary report data."""

from pathlib import Path
import argparse
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def simulated_provider(api_key, context):
    assert api_key == "sk-browser-test-not-a-real-key-12345"
    assert len(context["baseline_answers"]) == 20
    assert len(context["follow_up_answers"]) == 6
    return {
        "user_reflection": "Your test answers describe feeling overloaded by the news. You named gardening as a way to reset. Keeping a short news window can leave room for that activity.",
        "primary_stressor_category": "News Fatigue",
        "strategies": [
            {"title": "Make room for gardening", "description": "Try the gardening break you described after a scheduled news check-in."},
            {"title": "Keep an evening news window", "description": "Use the short evening check-in from your written plan."},
            {"title": "Choose one local step", "description": "Keep your next civic action small enough to fit your schedule."},
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--empty-health-once", action="store_true")
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="civicbalance-browser-") as directory:
        server.REPORT_FILE = Path(directory) / "report.tsv"
        server.REPORT_ACCESS_TOKEN = "browser-test-staff"
        server.generate_recommendations = simulated_provider
        server.app.config["SECRET_KEY"] = "isolated-browser-test-only"
        if options.empty_health_once:
            normal_health = server.app.view_functions["health"]
            health_calls = 0

            def health_with_one_empty_response():
                global health_calls
                health_calls += 1
                if health_calls == 1:
                    return "", 403
                return normal_health()

            server.app.view_functions["health"] = health_with_one_empty_response
        server.app.run(host="127.0.0.1", port=8765, use_reloader=False)
