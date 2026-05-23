#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import mimetypes
import os
import re
import secrets
import shutil
import smtplib
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path


MAX_CAPTURE = 1024 * 1024


def run_command(argv, cwd, timeout=7200):
    started = dt.datetime.now()
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=False,
        )
        return {
            "argv": list(argv),
            "cwd": str(cwd),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-MAX_CAPTURE:],
            "stderr": proc.stderr[-MAX_CAPTURE:],
            "elapsed_seconds": round((dt.datetime.now() - started).total_seconds(), 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": list(argv),
            "cwd": str(cwd),
            "returncode": 124,
            "stdout": (exc.stdout or "")[-MAX_CAPTURE:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-MAX_CAPTURE:] if isinstance(exc.stderr, str) else "",
            "elapsed_seconds": round((dt.datetime.now() - started).total_seconds(), 3),
            "timed_out": True,
        }


def truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def split_words(value):
    return [item for item in re.split(r"[\s,]+", str(value or "").strip()) if item]


def slugify(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-._") or "plot"


class TemplateMap(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render_template(template, context):
    return str(template or "").replace("\\n", "\n").format_map(TemplateMap(context))


def git_root(path):
    result = run_command(["git", "rev-parse", "--show-toplevel"], path, timeout=15)
    if result["returncode"] == 0 and result["stdout"].strip():
        return Path(result["stdout"].strip()).resolve()
    return Path(path).resolve().parent


def load_postprocess_settings(mkexp2, experiment_dir):
    probe = run_command([str(mkexp2), "probe", "--all", "--run-properties"], experiment_dir, timeout=120)
    if probe["returncode"] != 0:
        raise RuntimeError(probe["stderr"].strip() or probe["stdout"].strip() or "mkexp2 probe failed")
    payload = json.loads(probe["stdout"] or "{}")
    experiments = payload.get("experiments") or []
    merged = {}
    enabled = False
    for experiment in experiments:
        props = (experiment.get("resolved") or {}).get("run_properties") or {}
        if truthy(props.get("postprocess.auto")):
            enabled = True
        for key, value in props.items():
            if key.startswith("postprocess.") and value not in ("", None):
                merged[key] = value
    merged["postprocess.auto"] = enabled
    return merged, probe


def plot_catalog(mkexp2, experiment_dir):
    result = run_command([str(mkexp2), "plot", "--list", "--json"], experiment_dir, timeout=60)
    if result["returncode"] != 0:
        raise RuntimeError(result["stderr"].strip() or result["stdout"].strip() or "mkexp2 plot --list failed")
    payload = json.loads(result["stdout"] or "{}")
    return {item["id"]: item for item in payload.get("plots") or []}, result


def selected_plot_ids(settings, catalog):
    raw = str(settings.get("postprocess.plots") or "default").strip()
    if not raw or raw == "default":
        return [plot_id for plot_id, item in catalog.items() if item.get("default_selected")]
    if raw == "all":
        return list(catalog)
    return split_words(raw)


def result_sources(experiment_dir):
    results = Path(experiment_dir) / "results"
    if not results.is_dir():
        return []
    sources = []
    for csv_file in sorted(results.glob("*.csv")):
        if csv_file.stat().st_size <= 0:
            continue
        sources.append({"name": csv_file.stem, "file": csv_file.name, "path": csv_file})
    return sources


def read_plot_index(experiment_dir):
    path = Path(experiment_dir) / "plots" / "index.json"
    if not path.is_file():
        return {"version": 1, "artifacts": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload.get("artifacts"), list):
        payload["artifacts"] = []
    payload["version"] = 1
    return payload


def write_plot_index(experiment_dir, index):
    plots_dir = Path(experiment_dir) / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    path = plots_dir / "index.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def source_sets_for_plot(plot, sources):
    max_sources = plot.get("max_sources")
    if max_sources is not None and int(max_sources) == 1 and len(sources) > 1:
        return [[source] for source in sources]
    return [sources]


def generate_plots(mkexp2, experiment_dir, run_id, settings, catalog):
    sources = result_sources(experiment_dir)
    plot_ids = selected_plot_ids(settings, catalog)
    no_docker = truthy(settings.get("postprocess.plot.no_docker"))
    threads = str(settings.get("postprocess.plot.threads") or "").strip()
    index = read_plot_index(experiment_dir)
    created = []
    commands = []
    skipped = []
    set_created_at = dt.datetime.now().isoformat(timespec="seconds")
    set_label = f"Automatic postprocess {run_id}"
    set_id = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-postprocess-{slugify(run_id)}"

    for plot_id in plot_ids:
        plot = catalog.get(plot_id)
        if not plot:
            skipped.append({"plot_id": plot_id, "reason": "unknown plot type"})
            continue
        for source_group in source_sets_for_plot(plot, sources):
            min_sources = int(plot.get("min_sources") or 0)
            max_sources = plot.get("max_sources")
            if len(source_group) < min_sources:
                skipped.append({"plot_id": plot_id, "reason": f"requires at least {min_sources} source(s)"})
                continue
            if max_sources is not None and len(source_group) > int(max_sources):
                skipped.append({"plot_id": plot_id, "reason": f"accepts at most {max_sources} source(s)"})
                continue

            source_label = "-".join(slugify(source["name"]) for source in source_group)
            artifact_id = "-".join(
                [
                    dt.datetime.now().strftime("%Y%m%d-%H%M%S"),
                    slugify(plot_id),
                    source_label[:48],
                    secrets.token_urlsafe(4).replace("_", "-"),
                ]
            )
            rel_output = f"plots/{artifact_id}.pdf"
            argv = [str(mkexp2), "plot", "--plot", plot_id, "--output", rel_output]
            if no_docker:
                argv.append("--no-docker")
            if threads:
                argv.extend(["--threads", threads])
            argv.extend(source["name"] for source in source_group)
            command = run_command(argv, experiment_dir, timeout=7200)
            commands.append({"plot_id": plot_id, "sources": [source["name"] for source in source_group], "command": command})
            pdf = Path(experiment_dir) / rel_output
            if command["returncode"] != 0 or not pdf.is_file() or pdf.stat().st_size <= 0:
                continue
            stat = pdf.stat()
            label = f"{plot.get('name', plot_id)} - {', '.join(source['name'] for source in source_group)}"
            artifact = {
                "id": artifact_id,
                "label": label,
                "plot_id": plot_id,
                "plot_name": plot.get("name", plot_id),
                "description": plot.get("description", ""),
                "plot_set_id": set_id,
                "plot_set_label": set_label,
                "plot_set_created_at": set_created_at,
                "sources": [
                    {"kind": "algorithm", "name": source["name"], "alias": source["name"], "experiment_id": Path(experiment_dir).name}
                    for source in source_group
                ],
                "path": rel_output,
                "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                "size": stat.st_size,
                "modified_at": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }
            index.setdefault("artifacts", []).append(artifact)
            created.append(artifact)
    write_plot_index(experiment_dir, index)
    return {"created": created, "commands": commands, "skipped": skipped, "sources": sources, "plot_ids": plot_ids}


def email_recipients(settings):
    return split_words(settings.get("postprocess.email.to"))


def email_context(experiment_dir, repo, status, parse_result, plots_result, started_at, finished_at):
    plot_paths = [str(Path(experiment_dir) / item["path"]) for item in plots_result.get("created") or []]
    return {
        "experiment": Path(experiment_dir).name,
        "experiment_id": Path(experiment_dir).name,
        "experiment_path": str(experiment_dir),
        "repo": str(repo),
        "status": status,
        "plot_count": str(len(plot_paths)),
        "plots": "\n".join(plot_paths) or "(none)",
        "parse_returncode": str((parse_result or {}).get("returncode", "")),
        "started_at": started_at,
        "finished_at": finished_at,
    }


def send_email(settings, context, attachments):
    recipients = email_recipients(settings)
    if not recipients:
        return {"sent": False, "reason": "postprocess.email.to is empty"}

    sender = str(settings.get("postprocess.email.from") or f"mkexp2@{os.uname().nodename}").strip()
    subject_template = settings.get("postprocess.email.subject") or "mkexp2 {status}: {experiment_id}"
    body_template = settings.get("postprocess.email.body") or (
        "Experiment: {experiment_id}\n"
        "Status: {status}\n"
        "Path: {experiment_path}\n\n"
        "Generated plots:\n{plots}\n"
    )
    subject = render_template(subject_template, context)
    body = render_template(body_template, context)
    attach_plots = truthy(settings.get("postprocess.email.attach_plots", "true"))
    attachment_paths = [Path(path) for path in attachments if attach_plots and Path(path).is_file()]

    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)
    for path in attachment_paths:
        ctype, _ = mimetypes.guess_type(str(path))
        maintype, subtype = (ctype or "application/pdf").split("/", 1)
        message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)

    sendmail = shutil.which("sendmail") or ("/usr/sbin/sendmail" if Path("/usr/sbin/sendmail").exists() else "")
    if sendmail:
        proc = subprocess.run([sendmail, "-t"], input=message.as_string(), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return {"sent": proc.returncode == 0, "method": sendmail, "returncode": proc.returncode, "stderr": proc.stderr}

    smtp_host = os.environ.get("MKEXP2_SMTP_HOST")
    if smtp_host:
        with smtplib.SMTP(smtp_host) as smtp:
            smtp.send_message(message)
        return {"sent": True, "method": f"smtp:{smtp_host}"}

    mail = shutil.which("mail") or shutil.which("mailx")
    if mail and not attachment_paths:
        proc = subprocess.run([mail, "-s", subject, *recipients], input=body, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return {"sent": proc.returncode == 0, "method": mail, "returncode": proc.returncode, "stderr": proc.stderr}

    return {"sent": False, "reason": "no sendmail, MKEXP2_SMTP_HOST, or attachment-capable mailer found"}


def main():
    parser = argparse.ArgumentParser(description="Run mkexp2 postprocessing after generated jobs finish.")
    parser.add_argument("--mkexp2", required=True)
    parser.add_argument("--experiment-dir", required=True)
    parser.add_argument("--run-id", default=dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()

    mkexp2 = Path(args.mkexp2).resolve()
    experiment_dir = Path(args.experiment_dir).resolve()
    started_at = dt.datetime.now().isoformat(timespec="seconds")
    repo = git_root(experiment_dir)
    settings, probe = load_postprocess_settings(mkexp2, experiment_dir)
    print(json.dumps({"event": "settings", "enabled": truthy(settings.get("postprocess.auto")), "settings": settings}, indent=2))
    if not truthy(settings.get("postprocess.auto")):
        print("postprocess.auto is not enabled; nothing to do")
        return 0

    parse_result = None
    if truthy(settings.get("postprocess.parse", "true")):
        print("==> Parsing logs")
        parse_result = run_command([str(mkexp2), "parse"], experiment_dir, timeout=3600)
        print(json.dumps({"event": "parse", "returncode": parse_result["returncode"]}, indent=2))

    print("==> Generating plots")
    catalog, catalog_command = plot_catalog(mkexp2, experiment_dir)
    plots_result = generate_plots(mkexp2, experiment_dir, args.run_id, settings, catalog)
    failed_plots = [
        item for item in plots_result["commands"]
        if item["command"]["returncode"] != 0
    ]
    status = "completed"
    if parse_result and parse_result["returncode"] != 0:
        status = "parse failed"
    elif failed_plots:
        status = "plot failed"
    print(json.dumps({
        "event": "plots",
        "created": [item["path"] for item in plots_result["created"]],
        "skipped": plots_result["skipped"],
        "failed": len(failed_plots),
    }, indent=2))

    finished_at = dt.datetime.now().isoformat(timespec="seconds")
    context = email_context(
        experiment_dir,
        repo,
        status,
        parse_result,
        plots_result,
        started_at,
        finished_at,
    )
    attachments = [str(experiment_dir / item["path"]) for item in plots_result.get("created") or []]
    email_result = send_email(settings, context, attachments)
    print(json.dumps({"event": "email", **email_result}, indent=2))

    return 0 if status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
