from __future__ import annotations

import csv
import os
import re
import secrets
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from flask import Flask, abort, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

from recommendations import RecommendationError, generate_recommendations

BASE_DIR = Path(__file__).resolve().parent
QUESTION_FILE = BASE_DIR/"questions.txt"
PATHWAY_FILE = BASE_DIR/"pathways.txt"
REPORT_FILE = BASE_DIR/"constituent_sentiment.txt"

REPORT_LOCK = Lock()
REPORT_FIELDS = ["date_utc", "pathway", "A", "B", "C", "D", "E", "F"]
MIN_REPORT_SAMPLE = 1 # change this to 5 for demo


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=80 * 1024)
REPORT_ACCESS_TOKEN = os.environ.get("REPORT_ACCESS_TOKEN", "showme")

def load_questionnaire():
    sections, section, question = [], None, None
    for line_number, raw in enumerate(QUESTION_FILE.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match_section = re.fullmatch(r"\[SECTION ([A-Z])\]", line)
        match_question = re.match(r"^Q(\d+):\s*(.+)$", line)
        if match_section:
            section = {"id": match_section.group(1), "name": "", "questions": []}
            sections.append(section)
            question = None
        elif line.startswith("Name:"):
            if section is None:
                raise ValueError(f"Line {line_number}: missing section")
            section["name"] = line.partition(":")[2].strip()
        elif match_question:
            if section is None:
                raise ValueError(f"Line {line_number}: missing section")
            question = {"id": int(match_question.group(1)), "text": match_question.group(2), "options": []}
            section["questions"].append(question)
        elif "|" in line:
            if question is None:
                raise ValueError(f"Line {line_number}: missing question")
            raw_score, _, label = line.partition("|")
            score = int(raw_score.strip())
            if score not in range(5) or not label.strip():
                raise ValueError(f"Line {line_number}: invalid answer")
            question["options"].append({"label": label.strip(), "score": score})
        else:
            raise ValueError(f"Line {line_number}: unrecognized text")
    ids = [q["id"] for s in sections for q in s["questions"]]
    if [s["id"] for s in sections] != list("ABCDEF") or sorted(ids) != list(range(1, 21)):
        raise ValueError("Expected sections A–F and questions 1–20")
    for section in sections:
        if not section["name"]:
            raise ValueError("Section name missing")
        for q in section["questions"]:
            if sorted(o["score"] for o in q["options"]) != list(range(5)):
                raise ValueError(f"Q{q['id']}: expected five scores 0–4")
    return sections


def load_pathways():
    pathways, current = {}, None
    for line_number, raw in enumerate(PATHWAY_FILE.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        heading = re.fullmatch(r"\[PATHWAY ([123])\]", line)
        question = re.fullmatch(r"Q(2[1-6]):\s*(.+)", line)
        if heading:
            number = int(heading.group(1))
            if number in pathways:
                raise ValueError("Duplicate pathway")
            current = {"id": number, "name": "", "questions": []}
            pathways[number] = current
        elif line.startswith("Name:") and current:
            current["name"] = line.partition(":")[2].strip()
        elif question and current:
            current["questions"].append({"id": int(question.group(1)), "text": question.group(2)})
        else:
            raise ValueError(f"Pathways line {line_number}: unrecognized text")
    if set(pathways) != {1, 2, 3}:
        raise ValueError("Expected pathways 1, 2, 3")
    for pathway in pathways.values():
        if not pathway["name"] or sorted(q["id"] for q in pathway["questions"]) != list(range(21, 27)):
            raise ValueError("Each pathway needs Q21–Q26 and a name")
    return pathways


def score_answers(sections, answers):
    if not isinstance(answers, dict) or set(answers) != {str(i) for i in range(1, 21)}:
        raise ValueError("Please answer all 20 questions")
    results = []
    for section in sections:
        total = 0
        for q in section["questions"]:
            index = answers[str(q["id"])]
            if type(index) is not int or not 0 <= index < len(q["options"]):
                raise ValueError(f"Invalid response to question {q['id']}")
            total += q["options"][index]["score"]
        maximum = len(section["questions"]) * 4
        results.append({"id": section["id"], "name": section["name"], "raw_score": total,
                        "max_score": maximum, "normalized_score": round(100 * total / maximum, 1)})
    return results


def select_pathway(scores):
    """Daksh integration: strongest equally weighted pair of normalized domains.

    Ties prefer the pair with the strongest individual domain, then the lowest
    pathway number. Routing is reproducible and does not require an API call.
    """
    domains = {score["id"]: score["normalized_score"] for score in scores}
    pairs = {1: ("A", "B"), 2: ("C", "E"), 3: ("D", "F")}
    priorities = {number: round(sum(domains[d] for d in pair) / 2, 2) for number, pair in pairs.items()}
    chosen = max(pairs, key=lambda p: (priorities[p], max(domains[d] for d in pairs[p]), -p))
    return chosen, priorities


def suggestions(pathway, scores, baseline_answers, written_answers, api_key):
    """Daksh integration: personalize from all 26 actual answers and scores."""
    baseline = []
    for section in load_questionnaire():
        for question in section["questions"]:
            option = question["options"][baseline_answers[str(question["id"])]]
            baseline.append({"id": question["id"], "question": question["text"], "answer": option["label"]})
    context = {
        "baseline_answers": baseline,
        "domain_scores": scores,
        "selected_pathway": {"id": pathway["id"], "name": pathway["name"]},
        "follow_up_answers": [
            {"id": q["id"], "question": q["text"], "answer": written_answers[str(q["id"])].strip()}
            for q in pathway["questions"]
        ],
    }
    return generate_recommendations(api_key, context)


def brief_evaluation(scores, pathway, personalized):
    """Summarize observed domain scores and one personalized next step."""
    highest = max(score["normalized_score"] for score in scores)
    lowest = min(score["normalized_score"] for score in scores)
    if highest == lowest:
        points = [
            f"All six domain scores are {highest:g}/100; no single domain scores higher than the others.",
            f"Your follow-up reflection focused on {pathway['name'].lower()}.",
        ]
    else:
        highest_names = ", ".join(score["name"] for score in scores if score["normalized_score"] == highest)
        lowest_names = ", ".join(score["name"] for score in scores if score["normalized_score"] == lowest)
        points = [
            f"Highest reported friction: {highest_names} ({highest:g}/100).",
            f"Lowest reported friction: {lowest_names} ({lowest:g}/100).",
        ]
    points.append(f"Suggested first step: {personalized['strategies'][0]['title'].rstrip('.') }.")
    return points


def append_aggregate(scores, pathway):
    """Store only six numerical domain scores + pathway, never free text or identities."""
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {"date_utc": datetime.now(timezone.utc).date().isoformat(), "pathway": pathway}
    record.update({score["id"]: score["normalized_score"] for score in scores})
    with REPORT_LOCK:
        needs_header = not REPORT_FILE.exists() or REPORT_FILE.stat().st_size == 0
        with REPORT_FILE.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS, delimiter="\t")
            if needs_header:
                writer.writeheader()
            writer.writerow(record)


def read_aggregates():
    if not REPORT_FILE.exists():
        return []
    with REPORT_LOCK:
        with REPORT_FILE.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle, delimiter="\t"))


@app.get("/")
def index():
    return render_template("index.html")


@app.after_request
def private_api_responses(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.errorhandler(HTTPException)
def api_http_error(error):
    if not request.path.startswith("/api/"):
        return error
    messages = {
        403: "Access denied. Check the staff report token.",
        404: "This CivicBalance endpoint is unavailable. Restart the updated server and refresh the page.",
        413: "The request is too large. Keep each follow-up answer within 2000 characters.",
        500: "CivicBalance could not complete this request. Please try again or check the server terminal.",
    }
    return jsonify({"error": messages.get(error.code, "CivicBalance could not process this request.")}), error.code


@app.get("/api/health")
def health():
    return jsonify({"application": "CivicBalance", "status": "ok"})


@app.get("/api/questions")
def get_questions():
    try:
        sections = load_questionnaire()
        return jsonify({"groups": [{"questions": [
            {"id": q["id"], "text": q["text"], "options": [o["label"] for o in q["options"]]}
            for q in section["questions"]]} for section in sections]})
    except (OSError, ValueError):
        app.logger.exception("Questionnaire configuration error")
        return jsonify({"error": "Questionnaire is temporarily unavailable"}), 500


@app.post("/api/submit")
def submit():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected a JSON object"}), 400
    try:
        scores = score_answers(load_questionnaire(), payload.get("answers"))
        pathways = load_pathways()
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError:
        return jsonify({"error": "Questionnaire is temporarily unavailable"}), 500

    pathway_id, priorities = select_pathway(scores)
    session.clear()
    session["phase1"] = scores
    session["pathway"] = pathway_id
    session["baseline_answers"] = payload["answers"]
    pathway = pathways[pathway_id]
    return jsonify({"results": scores, "pathway": {
        "id": pathway_id, "name": pathway["name"], "questions": pathway["questions"]},
        "routing_priorities": priorities})


@app.post("/api/complete")
def complete():
    scores = session.get("phase1")
    pathway_id = session.get("pathway")
    if not scores or pathway_id not in (1, 2, 3):
        return jsonify({"error": "Please complete the initial questionnaire first"}), 400
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected a JSON object"}), 400
    answers = payload.get("answers")
    if not isinstance(answers, dict) or set(answers) != {str(i) for i in range(21, 27)}:
        return jsonify({"error": "Please answer all six follow-up questions"}), 400
    if any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in answers.values()):
        return jsonify({"error": "Each response must be 1–2000 characters"}), 400
    if type(payload.get("share_aggregate")) is not bool:
        return jsonify({"error": "Please choose whether to share anonymous summary data"}), 400
    if payload.get("ai_consent") is not True:
        return jsonify({"error": "Please agree to send your answers to OpenAI for this reflection"}), 400
    api_key = request.headers.get("X-OpenAI-Key", "").strip()
    if not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,509}", api_key):
        return jsonify({"error": "Enter a valid OpenAI API key in the key field"}), 400
    baseline_answers = session.get("baseline_answers")
    if not isinstance(baseline_answers, dict):
        return jsonify({"error": "Please restart the initial questionnaire"}), 400
    try:
        pathway = load_pathways()[pathway_id]
        personalized = suggestions(pathway, scores, baseline_answers, answers, api_key)
    except RecommendationError as error:
        return jsonify({"error": str(error)}), error.status
    except (OSError, ValueError, KeyError, IndexError):
        return jsonify({"error": "Questionnaire configuration changed. Please restart the questionnaire."}), 500
    try:
        if payload["share_aggregate"]:
            append_aggregate(scores, pathway_id)
    except OSError:
        return jsonify({"error": "The aggregate report could not be saved. Please try again."}), 503
    session.clear()
    return jsonify({"results": scores, "pathway_name": pathway["name"],
                    "user_reflection": personalized["user_reflection"],
                    "primary_stressor_category": personalized["primary_stressor_category"],
                    "strategies": personalized["strategies"],
                    "evaluation": brief_evaluation(scores, pathway, personalized),
                    "shared": payload["share_aggregate"]})


@app.get("/report")
def report():
    return render_template("report.html")


@app.get("/api/report")
def report_data():
    expected = REPORT_ACCESS_TOKEN
    provided = request.headers.get("X-Report-Token", "")
    if not secrets.compare_digest(expected.encode("utf-8"), provided.encode("utf-8")):
        abort(403)
    rows = read_aggregates()
    count = len(rows)
    if count < MIN_REPORT_SAMPLE:
        return jsonify({"count": count, "minimum": MIN_REPORT_SAMPLE, "ready": False})
    domain_means = {}
    for domain in "ABCDEF":
        domain_means[domain] = round(sum(float(row[domain]) for row in rows) / count, 1)
    counts = Counter(int(row["pathway"]) for row in rows)
    # Only publish pathway counts large enough to avoid disclosing small groups.
    pathway_counts = {str(p): counts[p] if counts[p] >= MIN_REPORT_SAMPLE else None for p in (1, 2, 3)}
    return jsonify({"count": count, "minimum": MIN_REPORT_SAMPLE, "ready": True,
                    "domain_means": domain_means, "pathway_counts": pathway_counts,
                    "note": "Self-selected respondents; not a representative constituent sample."})


# Run the server
if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5050")))
