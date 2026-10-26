import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from recommendations import RecommendationError


TEST_KEY = "sk-test-key-for-automated-checks-only"
PERSONALIZED = {
    "user_reflection": "You described feeling overloaded. Gardening helps you reconnect with your surroundings. A manageable news schedule can make room for that activity.",
    "primary_stressor_category": "News Fatigue",
    "strategies": [
        {"title": "Make room for gardening", "description": "Use the gardening break you described after a planned news check-in."},
        {"title": "Keep your news window", "description": "Try the short evening check-in you suggested."},
        {"title": "Protect one priority", "description": "Choose a manageable action related to the issue you identified."},
    ],
}


def answers_for(levels=None):
    levels = levels or {}
    return {
        str(q["id"]): next(i for i, option in enumerate(q["options"]) if option["score"] == levels.get(section["id"], 0))
        for section in server.load_questionnaire() for q in section["questions"]
    }


def follow_ups(share=False):
    return {
        "answers": {str(i): f"Private synthetic reflection {i}; I enjoy gardening." for i in range(21, 27)},
        "share_aggregate": share, "ai_consent": True,
    }


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.report_file = Path(self.directory.name) / "report.tsv"
        for name, value in (("REPORT_FILE", self.report_file), ("REPORT_ACCESS_TOKEN", "staff-test"), ("MIN_REPORT_SAMPLE", 1)):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        server.app.config.update(TESTING=True, SECRET_KEY="synthetic-test-session-only")
        self.client = server.app.test_client()
        patcher = patch.object(server, "generate_recommendations", return_value=PERSONALIZED)
        self.provider = patcher.start()
        self.addCleanup(patcher.stop)

    def submit(self, levels=None):
        return self.client.post("/api/submit", json={"answers": answers_for(levels)})

    def complete(self, payload=None, key=TEST_KEY):
        return self.client.post("/api/complete", json=payload or follow_ups(), headers={"X-OpenAI-Key": key})

    def test_questionnaire_has_twenty_questions_and_reverse_scoring_is_preserved(self):
        response = self.client.get("/api/questions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sum(len(group["questions"]) for group in response.json["groups"]), 20)
        scores = server.score_answers(server.load_questionnaire(), answers_for())
        self.assertTrue(all(score["normalized_score"] == 0 for score in scores))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_health_identifies_civicbalance_and_does_not_call_openai(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"application": "CivicBalance", "status": "ok"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.provider.assert_not_called()

    def test_http_errors_return_json_instead_of_an_html_or_empty_body(self):
        denied = self.client.get("/api/report")
        missing = self.client.get("/api/missing-endpoint")
        oversized = self.client.post("/api/submit", data="x" * (81 * 1024), content_type="application/json")
        for response, expected in ((denied, 403), (missing, 404), (oversized, 413)):
            with self.subTest(status=expected):
                self.assertEqual(response.status_code, expected)
                self.assertIsInstance(response.json.get("error"), str)
                self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_all_three_routes_complete_followups_personal_results_and_aggregate_report(self):
        cases = [(1, {"A": 4, "B": 4}), (2, {"C": 4, "E": 4}), (3, {"D": 4, "F": 4})]
        for pathway, levels in cases:
            with self.subTest(pathway=pathway):
                submitted = self.submit(levels)
                self.assertEqual(submitted.status_code, 200)
                self.assertEqual(submitted.json["pathway"]["id"], pathway)
                self.assertEqual([q["id"] for q in submitted.json["pathway"]["questions"]], list(range(21, 27)))
                finished = self.complete(follow_ups(share=True))
                self.assertEqual(finished.status_code, 200)
                self.assertEqual(finished.json["user_reflection"], PERSONALIZED["user_reflection"])
                self.assertEqual(len(finished.json["results"]), 6)
                self.assertEqual(len(finished.json["strategies"]), 3)
                self.assertEqual(len(finished.json["evaluation"]), 3)
                self.assertIn(PERSONALIZED["strategies"][0]["title"], finished.json["evaluation"][-1])
                self.assertNotIn(TEST_KEY, finished.get_data(as_text=True))
                with self.client.session_transaction() as state:
                    self.assertEqual(dict(state), {})
        report = self.client.get("/api/report", headers={"X-Report-Token": "staff-test"})
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json["count"], 3)
        self.assertEqual(report.json["pathway_counts"], {"1": 1, "2": 1, "3": 1})
        self.assertTrue(all(value == 33.3 for value in report.json["domain_means"].values()))
        saved = self.report_file.read_text()
        self.assertNotIn(TEST_KEY, saved)
        self.assertNotIn("gardening", saved)
        self.assertNotIn("reflection", saved)
        self.assertEqual(saved.splitlines()[0].split("\t"), server.REPORT_FIELDS)

    def test_personalization_receives_all_twenty_six_answers_but_not_key_in_prompt(self):
        self.submit({"A": 3, "B": 4})
        self.assertEqual(self.complete().status_code, 200)
        key, context = self.provider.call_args.args
        self.assertEqual(key, TEST_KEY)
        self.assertEqual(len(context["baseline_answers"]), 20)
        self.assertEqual(len(context["follow_up_answers"]), 6)
        self.assertIn("gardening", context["follow_up_answers"][0]["answer"])
        self.assertNotIn(TEST_KEY, json.dumps(context))
        self.assertFalse(self.report_file.exists())

    def test_ties_are_deterministic_and_prefer_strongest_domain_then_lowest_id(self):
        def scores(values):
            return [{"id": domain, "normalized_score": values.get(domain, 0)} for domain in "ABCDEF"]
        self.assertEqual(server.select_pathway(scores({}))[0], 1)
        self.assertEqual(server.select_pathway(scores({"A": 50, "B": 50, "C": 100}))[0], 2)
        self.assertEqual(server.select_pathway(scores({"A": 100, "C": 100}))[0], 1)

    def test_brief_evaluation_is_short_and_grounds_each_bullet_in_scores_or_suggestions(self):
        scores = server.score_answers(server.load_questionnaire(), answers_for({"A": 4, "B": 4}))
        points = server.brief_evaluation(scores, server.load_pathways()[1], PERSONALIZED)
        self.assertEqual(len(points), 3)
        self.assertIn("News Fatigue, Compassion Fatigue (100/100)", points[0])
        self.assertIn("(0/100)", points[1])
        self.assertTrue(all(len(point) <= 240 for point in points))
        self.assertEqual(points[-1], "Suggested first step: Make room for gardening.")

    def test_equal_scores_are_described_without_inventing_a_dominant_concern(self):
        scores = server.score_answers(server.load_questionnaire(), answers_for())
        points = server.brief_evaluation(scores, server.load_pathways()[1], PERSONALIZED)
        self.assertIn("All six domain scores are 0/100", points[0])
        self.assertIn("no single domain scores higher", points[0])
        self.assertNotIn("Highest reported friction", " ".join(points))

    def test_missing_invalid_or_boolean_baseline_answers_are_rejected(self):
        invalid = answers_for()
        invalid["1"] = True
        for payload in ({"answers": invalid}, {"answers": {}}, ["bad payload"]):
            with self.subTest(payload=type(payload).__name__):
                self.assertEqual(self.client.post("/api/submit", json=payload).status_code, 400)
        self.provider.assert_not_called()

    def test_key_consent_and_all_six_followups_are_required_before_api_call(self):
        self.submit()
        cases = []
        no_consent = follow_ups()
        no_consent["ai_consent"] = False
        cases.append((no_consent, TEST_KEY))
        cases.append((follow_ups(), ""))
        cases.append((follow_ups(), "not-an-api-key"))
        for value in ("", "   ", "x" * 2001, 3):
            payload = follow_ups()
            payload["answers"]["21"] = value
            cases.append((payload, TEST_KEY))
        missing = follow_ups()
        del missing["answers"]["21"]
        cases.append((missing, TEST_KEY))
        for payload, key in cases:
            with self.subTest(key_present=bool(key)):
                self.assertEqual(self.complete(payload, key).status_code, 400)
        self.provider.assert_not_called()
        self.assertFalse(self.report_file.exists())

    def test_api_failure_keeps_survey_for_retry_without_storing_key_or_text(self):
        self.submit()
        self.provider.side_effect = RecommendationError("OpenAI did not accept this API key.", 401)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            failed = self.complete(follow_ups(share=True))
        self.assertEqual(failed.status_code, 401)
        self.assertNotIn(TEST_KEY, output.getvalue() + failed.get_data(as_text=True))
        self.assertFalse(self.report_file.exists())
        with self.client.session_transaction() as state:
            self.assertIn("phase1", state)
            self.assertNotIn(TEST_KEY, json.dumps(dict(state)))
            self.assertNotIn("gardening", json.dumps(dict(state)))
        self.provider.side_effect = None
        self.assertEqual(self.complete(follow_ups(share=True)).status_code, 200)
        self.assertEqual(len(server.read_aggregates()), 1)
        self.assertEqual(self.complete(follow_ups(share=True)).status_code, 400)
        self.assertEqual(len(server.read_aggregates()), 1)

    def test_report_requires_token_and_suppresses_small_groups(self):
        self.assertEqual(self.client.get("/api/report").status_code, 403)
        for _ in range(5):
            server.append_aggregate(server.score_answers(server.load_questionnaire(), answers_for()), 1)
        with patch.object(server, "MIN_REPORT_SAMPLE", 5):
            report = self.client.get("/api/report", headers={"X-Report-Token": "staff-test"}).json
        self.assertTrue(report["ready"])
        self.assertEqual(report["pathway_counts"], {"1": 5, "2": None, "3": None})

    def test_html_includes_password_key_field_and_accurate_report_labels(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="openaiKey" type="password"', html)
        self.assertIn('id="aiConsent"', html)
        self.assertIn('id="userReflection"', html)
        self.assertIn('id="evaluationList"', html)
        self.assertLess(html.index('/static/api.js'), html.index('/static/app.js'))
        self.assertNotIn("Randomly assigned", self.client.get("/report").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
