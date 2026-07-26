"""The `tg` command line.

`tg demo` is the whole story in one keyless command: build a workspace, replay it
deterministically, score, catch seeded regressions, and open the report. The other
commands operate on that workspace (or one you point them at).
"""

from __future__ import annotations

import json
import os
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tracegym import __version__

app = typer.Typer(
    name="tg",
    help="TraceGym: record, replay, score, and gate AI agents at $0.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

DEMO_DIR = Path.home() / ".cache" / "tracegym" / "demo"


@app.callback()
def main() -> None:
    """Record, replay, score, and gate AI agents at $0."""


@app.command()
def version() -> None:
    """Print the installed TraceGym version."""
    console.print(__version__)


def _open(path: Path) -> None:
    try:
        webbrowser.open(path.resolve().as_uri())
    except Exception:
        console.print(f"Open the report manually: {path}")


def _ensure_workspace(workspace: Path):
    """Build the demo workspace if it is not present, then return its manifest."""
    from tracegym.demos.run import run_demo

    if not (workspace / "traces.db").exists():
        run_demo(workspace)
    return json.loads((workspace / "manifest.json").read_text())


@app.command()
def demo(
    open_report: bool = typer.Option(True, "--open/--no-open", help="Open the HTML report."),
    ephemeral: bool = typer.Option(False, "--ephemeral", help="Use a throwaway temp workspace."),
) -> None:
    """Run the full zero-key demo end to end and open the report."""
    from tracegym.demos.run import run_demo
    from tracegym.report import render_report

    workspace = Path(__import__("tempfile").mkdtemp(prefix="tracegym-")) if ephemeral else DEMO_DIR
    workspace.mkdir(parents=True, exist_ok=True)
    console.print(f"[dim]Building demo workspace at {workspace} ...[/dim]")
    bundle = run_demo(workspace)
    report = render_report(bundle, workspace / "report.html")

    r = bundle["recall"]
    d = bundle["determinism"]
    g = bundle["gate_demo"]
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_row("Golden cases", str(bundle["manifest"]["total_cases"]))
    table.add_row("Seeded regressions caught", f"{r['caught']}/{r['total']}")
    table.add_row("Replay determinism", f"{d['pct']}% ({d['identical']}/{d['cases']} identical)")
    table.add_row("Gate demo", f"{g['verdict']} on the {g['bug']} bug")
    table.add_row("Counterfactual spend", "$0.00 (free-tier)")
    console.print(Panel(table, title="TraceGym demo", border_style="green"))
    console.print(f"Report written: [bold]{report}[/bold]")
    if open_report:
        _open(report)
    console.print("[dim]Demo ran fully offline. To record your own agent: tg record --help[/dim]")


@app.command()
def report(
    workspace: Path = typer.Option(DEMO_DIR, help="Workspace to render."),
    open_report: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Render the HTML report for a workspace (builds the demo if none exists)."""
    from tracegym.demos.run import run_demo
    from tracegym.report import render_report

    bundle = run_demo(workspace)
    out = render_report(bundle, workspace / "report.html")
    console.print(f"Report written: [bold]{out}[/bold]")
    if open_report:
        _open(out)


def _with_workspace(workspace: Path):
    """Return (conn, baseline_run_id_by_suite) with cwd set inside the workspace."""
    from tracegym.store import connect

    _ensure_workspace(workspace)
    os.chdir(workspace)
    conn = connect("traces.db")
    baselines = {
        row["name"].replace("baseline-", ""): row["run_id"]
        for row in conn.execute("SELECT name, run_id FROM baselines")
    }
    return conn, baselines


def _baseline_or_exit(baselines: dict, suite: str) -> str:
    """Return the baseline run id for a suite, or print the options and exit."""
    if suite not in baselines:
        console.print(f"Unknown suite {suite!r}. Options: {', '.join(sorted(baselines))}")
        raise typer.Exit(2)
    return baselines[suite]


@app.command()
def profile(
    workspace: Path = typer.Option(DEMO_DIR),
    suite: str = typer.Option("sql-analyst"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show the cost/token/speed ledger for a suite's baseline run."""
    from tracegym.advisor import build_profile

    conn, baselines = _with_workspace(workspace)
    prof = build_profile(conn, _baseline_or_exit(baselines, suite))
    if json_out:
        console.print_json(json.dumps(prof))
        return
    t = prof["totals"]
    console.print(
        Panel(
            f"cost ${t['cost_usd']:.6f} | input {t['input_tokens']} tok | "
            f"out/in {t['oi_ratio']} | p95 {prof['latency_ms']['p95']}ms | "
            f"budget {prof['budget']['pct_used']}% of ${prof['budget']['cap_usd']}",
            title=f"profile: {suite}",
        )
    )


@app.command()
def advise(
    workspace: Path = typer.Option(DEMO_DIR),
    suite: str = typer.Option("sql-analyst"),
) -> None:
    """List validated efficiency recommendations for a suite's baseline run."""
    from tracegym.advisor import advise as run_advise
    from tracegym.demos import DEMO_ROSTER

    conn, baselines = _with_workspace(workspace)
    recs = run_advise(conn, _baseline_or_exit(baselines, suite), roster=DEMO_ROSTER)
    table = Table("rule", "recommendation", "status", "saving")
    for r in recs:
        table.add_row(
            r.rule_id, r.title, r.status, f"${r.est_saving_usd:.6f}" if r.est_saving_usd else "-"
        )
    console.print(table if recs else "No recommendations for this run.")


@app.command()
def gate(
    vs: str = typer.Option(
        "baseline", help="Reference to gate against: 'baseline' or a specific run id."
    ),
    workspace: Path = typer.Option(DEMO_DIR),
    suite: str = typer.Option("sql-analyst"),
    bug: str = typer.Option("sql_delete", help="Seeded bug variant to gate (demo)."),
) -> None:
    """Gate a seeded-bug variant against a reference run and exit non-zero on BLOCK."""
    from tracegym.demos.bugs import BUGS, buggy_agent
    from tracegym.demos.harness import agent_and_responder
    from tracegym.gate import gate_runs
    from tracegym.replay import run_suite
    from tracegym.replay.loader import load_suite

    conn, baselines = _with_workspace(workspace)
    reference = _baseline_or_exit(baselines, suite) if vs == "baseline" else vs
    if conn.execute("SELECT 1 FROM runs WHERE id = ?", (reference,)).fetchone() is None:
        console.print(f"No such reference run: {reference!r}. Use 'baseline' or a valid run id.")
        raise typer.Exit(2)
    cases, _ = load_suite(Path("suites") / suite)
    base_agent, responder = agent_and_responder(suite)
    spec = next((b for b in BUGS if b["id"] == bug), None)
    if spec is None:
        console.print(f"Unknown bug {bug!r}. Options: {', '.join(b['id'] for b in BUGS)}")
        raise typer.Exit(2)
    transform = spec["fn"]
    buggy = run_suite(
        conn,
        Path("blobs"),
        suite_id=suite,
        cases=cases,
        agent=buggy_agent(base_agent, transform),
        mode="frozen",
        responder=responder,
    )
    result = gate_runs(conn, buggy, reference)
    color = {"BLOCK": "red", "WARN": "yellow", "PASS": "green"}[result.verdict]
    console.print(
        Panel(
            "; ".join(result.reasons or result.warnings) or "no regression",
            title=f"[{color}]{result.verdict}[/{color}]",
            border_style=color,
        )
    )
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # Tiered annotations: BLOCK reasons fail the check, WARN warnings do not.
        for reason in result.reasons:
            print(f"::error title=TraceGym gate::{reason}")
        for warn in result.warnings:
            print(f"::warning title=TraceGym gate::{warn}")
    raise typer.Exit(1 if result.blocked else 0)


@app.command()
def calibrate(
    workspace: Path = typer.Option(DEMO_DIR),
    labeler: str = typer.Option(
        "canary-gold",
        help="Whose labels to calibrate against (e.g. reviewer for tg review labels).",
    ),
) -> None:
    """Report judge-vs-label agreement (Cohen's kappa) and the gate mode."""
    from tracegym.calibrate import calibrate_from_db

    conn, _ = _with_workspace(workspace)
    rep = calibrate_from_db(conn, labeler=labeler, min_labels=1)
    console.print_json(json.dumps(rep))


@app.command()
def review(
    action: str = typer.Argument("list", help="list | ack | resolve"),
    item_id: str = typer.Argument(None, help="review item id, for ack/resolve"),
    workspace: Path = typer.Option(DEMO_DIR),
    label: str = typer.Option(
        None, help="pass|fail: records a human label when resolving a needs_review item"
    ),
) -> None:
    """Show or clear the human-notify queue (the control loop's soft, no-block tier)."""
    from tracegym import review as rq

    conn, _ = _with_workspace(workspace)
    if action == "list":
        items = rq.list_open(conn)
        if not items:
            console.print("Review queue is empty. Nothing needs a human right now.")
            raise typer.Exit(0)
        table = Table("id", "kind", "severity", "ref", "reason")
        for it in items:
            table.add_row(
                it["id"],
                it["kind"],
                it.get("severity") or "",
                it.get("ref_id") or "",
                it.get("reason") or "",
            )
        console.print(table)
        return
    if not item_id:
        console.print("Provide an item id, for example: tg review resolve rv-... --label pass")
        raise typer.Exit(1)
    if action == "ack":
        console.print("Acknowledged." if rq.ack(conn, item_id) else "No such open item.")
        return
    if action == "resolve":
        label_pass = None
        if label is not None:
            if label not in ("pass", "fail"):
                console.print("--label must be pass or fail")
                raise typer.Exit(1)
            label_pass = label == "pass"
        res = rq.resolve(conn, item_id, label_pass=label_pass)
        if not res:
            console.print("No such item.")
        elif res.labeled:
            console.print(
                "Resolved and recorded a human label. Fold it into calibration with: "
                "tg calibrate --labeler reviewer"
            )
        elif label_pass is not None:
            console.print("Resolved. No label recorded: this item has no case output to label.")
        else:
            console.print("Resolved.")
        return
    console.print(f"Unknown action {action!r}. Use: list | ack | resolve.")
    raise typer.Exit(1)


_SPARK = "▁▂▃▄▅▆▇█"


def _sparkline(vals: list[float]) -> str:
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return _SPARK[0] * len(vals)
    steps = len(_SPARK) - 1
    return "".join(_SPARK[min(steps, int((v - lo) / (hi - lo) * steps))] for v in vals)


@app.command()
def trend(
    workspace: Path = typer.Option(DEMO_DIR),
    suite: str = typer.Option("sql-analyst"),
    metric: str = typer.Option(
        "mean_score", help="mean_score|task_success_rate|cost_usd|p95_latency_ms|invariant_failures"
    ),
    check: bool = typer.Option(False, "--check", help="Run the SPC drift check on the metric."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show a metric's trend across recent runs (sparkline + per-run delta)."""
    from tracegym.timeseries import METRICS, metric_series, run_history

    conn, _ = _with_workspace(workspace)
    hist = run_history(conn, suite)
    if metric not in METRICS:
        console.print(f"Unknown metric. Options: {', '.join(METRICS)}")
        raise typer.Exit(1)
    if json_out:
        console.print_json(json.dumps({"suite": suite, "metric": metric, "history": hist}))
        return
    if len(hist) < 2:
        console.print(f"Not enough run history for {suite} yet (need 2+ runs).")
        raise typer.Exit(0)
    vals = metric_series(hist, metric)
    label, direction = METRICS[metric]
    tag = " (illustrative)" if hist[0].get("synthetic") else ""
    console.print(f"[bold]{suite}[/bold] · {label} · {len(hist)} runs{tag}")
    console.print(f"  {_sparkline(vals)}  latest={vals[-1]:g}")
    table = Table("when", "commit", label, "delta")
    prev = None
    for h in hist:
        v = h[metric]
        table.add_row(
            h["created_at"], h["git_sha"], f"{v:g}", "" if prev is None else f"{v - prev:+g}"
        )
        prev = v
    console.print(table)
    if check:
        _print_drift(conn, suite, metric, label, vals, direction, hist[0].get("synthetic", False))


def _print_drift(conn, suite, metric, label, vals, direction, synthetic) -> None:
    from tracegym import review as rq
    from tracegym.spc import drift_check

    r = drift_check(vals, direction)
    status = r["status"]
    color = {"drift": "red", "recovered": "yellow"}.get(status, "green")
    line = f"SPC: [{color}]{status}[/{color}]"
    if r.get("change_point") is not None and status in ("drift", "recovered"):
        line += f" · most likely shift at run #{r['change_point'] + 1}"
    if status == "insufficient":
        line = f"SPC: need {r['min_samples']}+ runs (have {r['n']})"
    console.print(line)
    if status == "drift" and not synthetic:
        # Ongoing drift is a human-notify signal: route it to the review queue.
        rq.enqueue(
            conn,
            run_id=None,
            kind="drift",
            ref_id=f"{suite}:{metric}",
            reason=f"{label} drifting on {suite} (shift near run #{(r['change_point'] or 0) + 1})",
            severity="med",
        )
        console.print("  routed to the review queue (tg review)")


@app.command()
def cases(
    workspace: Path = typer.Option(DEMO_DIR),
    suite: str = typer.Option("sql-analyst"),
) -> None:
    """List a suite's cases with score, judge state, and invariant failures."""
    from tracegym.inspection import list_cases

    conn, baselines = _with_workspace(workspace)
    rows = list_cases(conn, _baseline_or_exit(baselines, suite))
    table = Table("case", "score", "judge", "invariant fails")
    for r in rows:
        table.add_row(
            r["case_id"], str(r["score"]), r["judge_state"] or "-", str(r["invariant_fail"])
        )
    console.print(table)


@app.command()
def show(
    case_id: str = typer.Argument(..., help="Case id, e.g. sql-001 or sr-003."),
    workspace: Path = typer.Option(DEMO_DIR),
    suite: str = typer.Option("", help="Limit to one suite; by default every suite is searched."),
) -> None:
    """Show one case in full: input, output, every check, judge, and trace."""
    from tracegym.inspection import case_detail
    from tracegym.replay.loader import load_suite
    from tracegym.report.render import render_output

    conn, baselines = _with_workspace(workspace)
    # Search the named suite first, then the rest, so a case id resolves without
    # the caller having to know which suite owns it.
    order = ([suite] if suite else []) + [s for s in baselines if s != suite]
    d = None
    for s in order:
        try:
            suite_cases, _ = load_suite(Path("suites") / s)
        except FileNotFoundError:
            continue
        case = next((c for c in suite_cases if c["id"] == case_id), None)
        if case is None:
            continue
        d = case_detail(conn, Path("blobs"), baselines[s], case_id, case)
        if d is not None:
            break
    if d is None:
        where = f"suite '{suite}'" if suite else "any suite"
        console.print(f"No case '{case_id}' in {where}.")
        raise typer.Exit(1)

    console.print(
        Panel(
            f"[bold]{case_id}[/bold]  score={d['score']}  {d['judge']['state'] or ''}", title="case"
        )
    )
    console.print(f"[dim]input[/dim] {d['input']}")
    console.print("[dim]output[/dim]")
    console.print(render_output(d["output"]))
    console.print("[dim]checks[/dim]")
    for ch in d["checks"]:
        mark = "[green]PASS[/green]" if ch["passed"] else "[red]FAIL[/red]"
        console.print(f"  {mark} {ch['name']}: {ch['detail']}")
    if d["judge"]["rationale"]:
        console.print(
            f"[dim]judge[/dim] {d['judge']['state']} ({d['judge']['confidence']}) {d['judge']['rationale']}"
        )
    console.print("[dim]trace[/dim]")
    for s in d["spans"]:
        console.print(
            f"  {s['kind']}:{s['name']}  {s['input_tokens']}+{s['output_tokens']} tok  "
            f"{s['latency_ms']}ms  ${s['cost_usd']:.6f}"
        )


@app.command()
def diff(
    bug: str = typer.Argument("sql_delete", help="Seeded bug id (see tracegym/demos/bugs.py)."),
    workspace: Path = typer.Option(DEMO_DIR),
) -> None:
    """Show a seeded bug: baseline vs buggy output and the check that caught it."""
    from tracegym.demos.bugs import BUGS
    from tracegym.inspection import regression_examples
    from tracegym.replay.loader import load_suite
    from tracegym.report.render import render_output

    conn, baselines = _with_workspace(workspace)
    suites = {}
    for sid in ("support-rag", "sql-analyst"):
        cs, _ = load_suite(Path("suites") / sid)
        suites[sid] = {"cases": cs, "baseline_run": baselines[sid]}
    gallery = regression_examples(conn, Path("blobs"), suites)
    g = next((x for x in gallery if x["id"] == bug), None)
    if g is None:
        console.print(f"Unknown bug '{bug}'. Options: {', '.join(b['id'] for b in BUGS)}")
        raise typer.Exit(1)

    color = "green" if g["caught"] else "red"
    console.print(
        Panel(
            f"{g['desc']} ({g['suite']})",
            title=f"[{color}]{'CAUGHT' if g['caught'] else 'MISSED'}[/{color}] {bug}",
            border_style=color,
        )
    )
    ex = g.get("example")
    if ex:
        console.print(f"[dim]case {ex['case_id']}[/dim]")
        console.print("[green]baseline output[/green]")
        console.print(render_output(ex["baseline_output"]))
        console.print("[red]buggy output[/red]")
        console.print(render_output(ex["buggy_output"]))
        caught = ", ".join(f"[red]{c['name']}[/red]" for c in ex["failing_checks"])
        console.print(f"[dim]caught by[/dim] {caught}")
        console.print(f"  {ex['failing_checks'][0]['detail']}")


@app.command()
def record(
    proxy: bool = typer.Option(False, "--proxy", help="Start the OpenAI-compatible capture proxy."),
    port: int = typer.Option(8080),
    db: Path = typer.Option(Path("runs/traces.db")),
) -> None:
    """Start the capture proxy so an agent's traffic is recorded (needs the proxy extra)."""
    if not proxy:
        console.print("Nothing to do. Use --proxy to start the capture proxy.")
        raise typer.Exit(1)
    try:
        import uvicorn

        from tracegym.capture.proxy import create_app
        from tracegym.store import connect
    except ImportError:
        # markup=False so Rich does not read the "[proxy]" extra as a style tag.
        console.print('The proxy needs the extra: pip install "tracegym[proxy]"', markup=False)
        raise typer.Exit(1) from None

    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db), check_same_thread=False)
    app_ = create_app(conn, db.parent / "blobs", mode="live")
    console.print(
        f"Capture proxy on http://127.0.0.1:{port} -> ${{TG_UPSTREAM_BASE_URL}} (stream=false only)"
    )
    uvicorn.run(app_, host="127.0.0.1", port=port)


if __name__ == "__main__":
    app()
