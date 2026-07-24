from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT.parent
INPUT_ROOT = AGENT_ROOT / "t2c_eval_framework" / "inputs"
BASE_URL = "http://127.0.0.1:8954"
DEFAULT_DBS = ("movies", "northwind", "recommendations", "twitter")
TRACE_STAGE_RE = re.compile(r"\[PipelineTrace:([^\]]+)\] \[([^\]]+)\] (.*)")


def _kill_existing_flask() -> None:
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'views/flask_api.py' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _request_json(path: str, payload: dict | None = None, timeout: int = 180) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_health(db: str, timeout: int = 90) -> dict:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            health = _request_json("/health", timeout=10)
            if health.get("status") == "ok" and health.get("database") == db:
                return health
            last_error = json.dumps(health, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - test helper should keep polling.
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(f"Flask health check failed for {db}: {last_error}")


def _start_flask(db: str, log_dir: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(
        {
            "NEO4J_DATABASE": db,
            "NEO4J_USERNAME": db,
            "NEO4J_PASSWORD": db,
            "PIPELINE_TRACE": "1",
            "PIPELINE_TRACE_MAX_CHARS": "40000",
        }
    )
    stdout = open(log_dir / f"{db}.server.out.log", "w", encoding="utf-8")
    stderr = open(log_dir / f"{db}.server.err.log", "w", encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, "views/flask_api.py"],
        cwd=ROOT,
        env=env,
        stdout=stdout,
        stderr=stderr,
    )


def _load_questions(db: str, limit: int) -> list[str]:
    path = INPUT_ROOT / f"{db}.csv"
    questions: list[str] = []
    csv.field_size_limit(sys.maxsize)
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            question = (row.get("question") or "").strip()
            if question:
                questions.append(question)
            if len(questions) >= limit:
                break
    return questions


def _sample_result(result: object) -> dict:
    if isinstance(result, list):
        return {"row_count": len(result), "sample_rows": result[:2]}
    return {"row_count": 0, "sample_rows": []}


def _collect_trace_stages(log_path: Path, trace_id: str) -> list[dict]:
    if not trace_id or not log_path.exists():
        return []
    stages: list[dict] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if f"[PipelineTrace:{trace_id}]" not in line:
            continue
        match = TRACE_STAGE_RE.search(line)
        if match:
            stages.append({"stage": match.group(2), "title": match.group(3).strip()})
    return stages


def _write_markdown(report_path: Path, rows: list[dict]) -> None:
    lines = [
        "# Pipeline Trace Samples",
        "",
        "This file records 10 text-to-Cypher calls per database with pipeline trace IDs.",
        "Each trace shows how the user question becomes a grounded schema context, then a final Cypher query.",
        "",
        "Important: the model's hidden reasoning is not available through the API. The trace records prompts, structured grounding output, validation decisions, final Cypher, and execution summaries.",
        "",
    ]
    db_order = list(dict.fromkeys(row["database"] for row in rows))
    for db in db_order:
        db_rows = [row for row in rows if row["database"] == db]
        lines.extend([f"## {db}", ""])
        for row in db_rows:
            status = "OK" if not row.get("error") else "ERROR"
            lines.extend(
                [
                    f"### {row['index']}. {row['question']}",
                    "",
                    f"- status: {status}",
                    f"- trace_id: `{row.get('trace_id') or ''}`",
                    f"- rows_returned: {row.get('row_count', 0)}",
                    f"- cypher: `{row.get('cypher_query') or ''}`",
                ]
            )
            if row.get("error"):
                lines.append(f"- error: `{row['error']}`")
            if row.get("stages"):
                stage_text = " -> ".join(stage["stage"] for stage in row["stages"])
                lines.append(f"- stage_flow: `{stage_text}`")
            lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    limit = int(os.getenv("TRACE_SAMPLE_LIMIT", "10"))
    dbs = tuple(
        db.strip()
        for db in os.getenv("TRACE_SAMPLE_DBS", ",".join(DEFAULT_DBS)).split(",")
        if db.strip()
    )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = ROOT / "outputs" / f"pipeline_trace_{stamp}"
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / "samples.jsonl"
    report_path = log_dir / "pipeline_trace_samples.md"

    rows: list[dict] = []
    _kill_existing_flask()

    try:
        for db in dbs:
            print(f"=== {db}: starting Flask with pipeline trace ===", flush=True)
            proc = _start_flask(db, log_dir)
            try:
                health = _wait_for_health(db)
                print(f"{db}: health={health}", flush=True)
                questions = _load_questions(db, limit)
                err_log = log_dir / f"{db}.server.err.log"
                for idx, question in enumerate(questions, start=1):
                    print(f"{db} #{idx}: {question}", flush=True)
                    started = time.time()
                    try:
                        response = _request_json(
                            "/api/text2cypher",
                            {"question": question},
                            timeout=240,
                        )
                    except urllib.error.HTTPError as exc:
                        body = exc.read().decode("utf-8", errors="replace")
                        response = {"error": f"HTTP {exc.code}: {body}"}
                    except Exception as exc:  # noqa: BLE001 - keep sample run going.
                        response = {"error": str(exc)}

                    trace_id = response.get("trace_id", "")
                    result_sample = _sample_result(response.get("result"))
                    row = {
                        "database": db,
                        "index": idx,
                        "question": question,
                        "trace_id": trace_id,
                        "elapsed_seconds": round(time.time() - started, 2),
                        "cypher_query": response.get("cypher_query", ""),
                        "error": response.get("error"),
                        **result_sample,
                    }
                    row["stages"] = _collect_trace_stages(err_log, trace_id)
                    rows.append(row)
                    with open(jsonl_path, "a", encoding="utf-8") as handle:
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=15)

        _write_markdown(report_path, rows)
        print(f"JSONL: {jsonl_path}", flush=True)
        print(f"Report: {report_path}", flush=True)
    finally:
        _kill_existing_flask()


if __name__ == "__main__":
    main()
