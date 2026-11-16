#!/usr/bin/env python3
"""Local interview practice state. Python 3.10+, standard library only."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile


FORMAT = "interview-bagu/v1"
TRACKS = {"mixed", "java", "agent", "extra", "all"}
PRIORITIES = ("P0", "P1", "P2")
DEFAULT_STATE = ".interview-bagu/progress.json"


def read_json(path):
    text = sys.stdin.read() if str(path) == "-" else Path(path).read_text(encoding="utf-8-sig")
    return json.loads(text)


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone.")
    return result


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError("IDs must contain 1-128 ASCII letters, digits, dots, underscores or hyphens.")
    return value


def nonempty(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Text values must be nonempty strings.")
    return value


def validate_questions(items):
    if not isinstance(items, list) or not items:
        raise ValueError("Questions must be a nonempty JSON array.")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"id", "track", "topic", "priority", "prompt"}:
            raise ValueError("Each question needs exactly id, track, topic, priority and prompt.")
        identifier(item["id"])
        if item["id"] in seen:
            raise ValueError("Question IDs must be unique.")
        seen.add(item["id"])
        if item["track"] not in {"java", "agent", "extra"} or item["priority"] not in PRIORITIES:
            raise ValueError("Invalid question track or priority.")
        nonempty(item["topic"])
        nonempty(item["prompt"])
    return items


def validate_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("Settings must be an object.")
    score = settings.get("pass_score")
    if type(score) not in (float, int) or not math.isfinite(score) or not 0 < score <= 10:
        raise ValueError("pass_score must be greater than 0 and at most 10.")
    for key in ("required_streak", "review_days"):
        if type(settings.get(key)) is not int or settings[key] < 1:
            raise ValueError(key + " must be a positive integer.")
    if settings["review_days"] > 36500:
        raise ValueError("review_days must be at most 36500.")
    if settings.get("track") not in TRACKS or settings.get("priority") not in PRIORITIES:
        raise ValueError("Invalid track or priority setting.")


def load_state(path):
    state = read_json(path)
    if not isinstance(state, dict) or state.get("format") != FORMAT:
        raise ValueError("Unsupported state format. Keep old records separate.")
    if type(state.get("revision")) is not int or state["revision"] < 0:
        raise ValueError("Invalid state revision.")
    validate_questions(state.get("questions"))
    validate_settings(state.get("settings"))
    timestamp(state["updated_at"])
    ids = {q["id"] for q in state["questions"]}
    current = state.get("current")
    if current is not None:
        if current["question_id"] not in ids or type(current["hinted"]) is not bool:
            raise ValueError("Invalid current question.")
        timestamp(current["asked_at"])
    if not isinstance(state.get("history"), list):
        raise ValueError("History must be an array.")
    attempts = set()
    for event in state["history"]:
        identifier(event["attempt_id"])
        if event["attempt_id"] in attempts or event["question_id"] not in ids:
            raise ValueError("Invalid history identity.")
        attempts.add(event["attempt_id"])
        timestamp(event["at"])
        if event["status"] == "scored":
            validate_payload({key: event[key] for key in PAYLOAD_KEYS})
            if type(event["independent"]) is not bool:
                raise ValueError("Invalid independent-answer flag.")
        elif event["status"] == "skipped":
            if event["score"] is not None:
                raise ValueError("Skipped attempts must have a null score.")
            nonempty(event["note"])
        else:
            raise ValueError("Unknown attempt status.")
    return state


@contextmanager
def write_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
    except FileExistsError:
        raise ValueError("State is locked. Read status first. Remove the lock only after verifying its writer stopped.")
    try:
        yield
    finally:
        lock.unlink()


def save_state(path, state):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def statistics(state):
    stats = {q["id"]: {"streak": 0, "best_streak": 0, "mastered": False,
                       "scored_attempts": 0, "last_score": None, "last_at": None,
                       "last_status": None, "next_review_at": None}
             for q in state["questions"]}
    rules = state["settings"]
    for event in state["history"]:
        item = stats[event["question_id"]]
        item["last_at"] = event["at"]
        item["last_status"] = event["status"]
        item["next_review_at"] = (timestamp(event["at"]) + timedelta(days=rules["review_days"])).isoformat()
        if event["status"] == "skipped":
            continue
        item["scored_attempts"] += 1
        item["last_score"] = event["score"]
        if event["score"] < rules["pass_score"]:
            item["streak"] = 0
        elif event["independent"]:
            item["streak"] += 1
        item["best_streak"] = max(item["best_streak"], item["streak"])
        item["mastered"] = item["streak"] >= rules["required_streak"]
    return stats


def choose(state, stats, now):
    settings = state["settings"]
    selected = {"mixed": {"java", "agent"}, "all": {"java", "agent", "extra"}}.get(
        settings["track"], {settings["track"]})
    eligible = [q for q in state["questions"] if q["track"] in selected
                and PRIORITIES.index(q["priority"]) <= PRIORITIES.index(settings["priority"])]
    due = [q for q in eligible if stats[q["id"]]["next_review_at"] is not None
           and timestamp(stats[q["id"]]["next_review_at"]) <= now]
    pool = due or [q for q in eligible if not stats[q["id"]]["mastered"]
                   and stats[q["id"]]["last_status"] != "skipped"]
    if not pool:
        return None
    if state["history"]:
        last_id = state["history"][-1]["question_id"]
        last_track = next(q["track"] for q in state["questions"] if q["id"] == last_id)
        alternate = [q for q in pool if q["track"] != last_track]
        pool = alternate or pool
    return min(pool, key=lambda q: (
        (stats[q["id"]]["next_review_at"] or "") if due else "",
        PRIORITIES.index(q["priority"]),
        stats[q["id"]]["streak"],
        stats[q["id"]]["last_at"] or "",
        q["id"]))


def status(state, now):
    stats = statistics(state)
    current = state["current"]
    current_view = None
    if current is not None:
        question = next(q for q in state["questions"] if q["id"] == current["question_id"])
        current_view = {**question, **current}
    return {"revision": state["revision"], "settings": state["settings"],
            "current": current_view, "next": choose(state, stats, now),
            "stats": stats, "history_count": len(state["history"])}


PAYLOAD_KEYS = {"attempt_id", "question_id", "score", "answer", "feedback"}


def validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS:
        raise ValueError("Score payload needs exactly attempt_id, question_id, score, answer and feedback.")
    identifier(payload["attempt_id"])
    identifier(payload["question_id"])
    score = payload["score"]
    if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 10:
        raise ValueError("Score must be a finite number between 0 and 10.")
    nonempty(payload["answer"])
    nonempty(payload["feedback"])
    return payload


def mutate(state, args, now, payload=None):
    """Return whether this operation changed state. Payload retries are idempotent."""
    if payload is not None:
        previous = next((e for e in state["history"] if e["attempt_id"] == payload["attempt_id"]), None)
        if previous is not None:
            if all(previous.get(key) == value for key, value in payload.items()):
                return False
            raise ValueError("Attempt ID already exists with different content.")
    if args.command == "ask" and state["current"] is not None:
        if args.question is None or args.question == state["current"]["question_id"]:
            if args.track is None and args.priority is None:
                return False
        raise ValueError("An unanswered question exists. Finish or explicitly skip it before switching.")
    if state["revision"] != args.expect_revision:
        raise ValueError("Revision conflict. Read status and reconcile before writing.")
    if args.command == "add":
        items = validate_questions(read_json(args.questions))
        existing = {q["id"]: q for q in state["questions"]}
        for item in items:
            if item["id"] in existing and existing[item["id"]] != item:
                raise ValueError("An existing question ID has different content. Use a new ID.")
        additions = [q for q in items if q["id"] not in existing]
        state["questions"].extend(additions)
        return bool(additions)
    if args.command == "ask":
        if args.track is not None:
            state["settings"]["track"] = args.track
        if args.priority is not None:
            state["settings"]["priority"] = args.priority
        question = choose(state, statistics(state), now)
        if args.question is not None:
            question = next((q for q in state["questions"] if q["id"] == args.question), None)
            if question is None:
                raise ValueError("Question ID is unknown.")
        if question is not None:
            state["current"] = {"question_id": question["id"], "asked_at": now.isoformat(), "hinted": False}
        return question is not None or args.track is not None or args.priority is not None
    current = state["current"]
    if current is None:
        raise ValueError("There is no unanswered question.")
    if args.command == "hint":
        changed = not current["hinted"]
        current["hinted"] = True
        return changed
    if payload["question_id"] != current["question_id"]:
        raise ValueError("Payload question does not match the unanswered question.")
    if now < timestamp(current["asked_at"]):
        raise ValueError("System time is earlier than question time. Check the clock.")
    event = {**payload, "at": now.isoformat()}
    if args.command == "record":
        event.update(status="scored", independent=not current["hinted"])
    else:
        event.update(status="skipped", score=None)
    state["history"].append(event)
    state["current"] = None
    return True


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--state", default=DEFAULT_STATE, help="Private local state file.")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create fresh state from local questions.")
    init.add_argument("--questions", required=True, help="Local question array JSON, or - for stdin.")
    init.add_argument("--pass-score", type=float, default=8)
    init.add_argument("--required-streak", type=int, default=5)
    init.add_argument("--review-days", type=int, default=3)
    init.add_argument("--track", choices=sorted(TRACKS), default="mixed")
    init.add_argument("--priority", choices=PRIORITIES, default="P0")
    commands.add_parser("status", help="Read progress and resume the current question.")
    for name in ("ask", "hint", "record", "skip", "add"):
        command = commands.add_parser(name)
        command.add_argument("--expect-revision", type=int, required=True)
        if name == "ask":
            command.add_argument("--question")
            command.add_argument("--track", choices=sorted(TRACKS))
            command.add_argument("--priority", choices=PRIORITIES)
        elif name == "record":
            command.add_argument("--payload", required=True, help="Local answer/score JSON, or - for stdin.")
        elif name == "skip":
            command.add_argument("--question", required=True)
            command.add_argument("--attempt-id", required=True, type=identifier)
            command.add_argument("--note", default="用户跳过", type=nonempty)
        elif name == "add":
            command.add_argument("--questions", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    path = Path(args.state)
    now = datetime.now(timezone.utc)
    try:
        if args.command == "status":
            result = status(load_state(path), now)
        else:
            payload = None
            if args.command == "record":
                payload = validate_payload(read_json(args.payload))
            elif args.command == "skip":
                payload = {"attempt_id": args.attempt_id, "question_id": identifier(args.question), "note": args.note}
            with write_lock(path):
                if args.command == "init":
                    if path.exists():
                        raise ValueError("State already exists. Initialization never overwrites records.")
                    settings = {"pass_score": args.pass_score, "required_streak": args.required_streak,
                                "review_days": args.review_days, "track": args.track, "priority": args.priority}
                    validate_settings(settings)
                    state = {"format": FORMAT, "revision": 0, "updated_at": now.isoformat(),
                             "settings": settings, "questions": validate_questions(read_json(args.questions)),
                             "current": None, "history": []}
                    changed = True
                else:
                    state = load_state(path)
                    changed = mutate(state, args, now, payload)
                    if changed:
                        state["revision"] += 1
                        state["updated_at"] = now.isoformat()
                if changed:
                    save_state(path, state)
                result = {**status(state, now), "changed": changed}
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
