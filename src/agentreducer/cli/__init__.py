"""Explicit command-line orchestration, with no import-time execution."""

import sys
import time

from agentreducer.reporting.models import CostSnapshot
from agentreducer.reporting.convert import success_report,failure_report,reduced_bytes
from agentreducer.reporting.json import render_json
from agentreducer.reporting.human import render_human
from agentreducer.reporting.output import publish,OutputFailure
from .parser import parse_cli,ParseFailure,InformationalExit
from .config import load_config
from .inputs import ConfigFailure
from .execution import prepare_execution
from .errors import classify_error,sanitized_stack


def _stdout(text):
    buffer=getattr(sys.stdout,'buffer',None)
    if buffer is not None:
        buffer.write(text.encode('utf-8'))
        buffer.flush()
    else:
        sys.stdout.write(text)
        sys.stdout.flush()


def _stderr(text):
    try:
        sys.stderr.write(text)
        sys.stderr.flush()
    except Exception:
        pass


def emit_parse_failure(failure):
    status,code,info=classify_error(ConfigFailure('INVALID_ARGUMENTS'))
    report=failure_report(failure.domain,status,code,info.mapping(),experiment=None,
        cost=CostSnapshot(),contexts={})
    try:
        _stdout(render_json(report) if failure.format=='json' else render_human(report))
    except Exception:
        _stderr('OUTPUT_FAILED: report delivery failed.\n')
        return 7
    return 2


def main(argv=None):
    # Only our two private parser exceptions are handled in the parse boundary.
    try:
        ns=parse_cli(argv)
    except InformationalExit as info:
        return info.code
    except ParseFailure as failure:
        return emit_parse_failure(failure)
    config=execution=result=None
    started=time.monotonic()
    configuration_failed=False
    try:
        config=load_config(ns,read_stdin=lambda:sys.stdin.buffer.read())
        execution=prepare_execution(config)
        result=execution.run()
        report=success_report(config.domain,result,experiment=config.safe_experiment,
            cost=execution.cost_snapshot(time.monotonic()-started),stochastic_decision=execution.stochastic_decision())
    except (Exception,KeyboardInterrupt) as exc:
        configuration_failed=isinstance(exc,ConfigFailure)
        status,code,info=classify_error(exc,execution)
        cost=execution.cost_snapshot(time.monotonic()-started) if execution else CostSnapshot(elapsed_seconds=time.monotonic()-started)
        report=failure_report(ns.domain,status,code,info.mapping(),
            experiment=config.safe_experiment if config else None,cost=cost,
            contexts=execution.contexts() if execution else {})
        if ns.debug and status=='internal-error':
            _stderr(sanitized_stack(exc))
    # Rendering is an implementation boundary; serialization failures are code6.
    try:
        json_text=render_json(report)
        text=json_text if ns.format=='json' else render_human(report)
        artifact=None
        output=config.output if config is not None and not configuration_failed else None
        if output is not None:
            if output.kind=='report':
                artifact=json_text.encode('utf-8')
            elif report.status=='reduced':
                artifact=reduced_bytes(config.domain,result)
    except Exception as exc:
        status,code,info=classify_error(exc)
        report=failure_report(ns.domain,status,code,info.mapping(),experiment=None,
            cost=report.cost,contexts={})
        text=render_json(report) if ns.format=='json' else render_human(report)
        output=artifact=None
        if ns.debug: _stderr(sanitized_stack(exc))
    try:
        if artifact is not None:
            publish(output,artifact)
    except (Exception,KeyboardInterrupt) as exc:
        if report.status=='cancelled':
            _stderr('OUTPUT_FAILED: cancelled report delivery failed; cancellation is retained.\n')
        else:
            mapped=exc if isinstance(exc,(KeyboardInterrupt,OutputFailure)) else OutputFailure("OUTPUT_PUBLISH_FAILED")
            status,code,info=classify_error(mapped)
            report=failure_report(ns.domain,status,code,info.mapping(),experiment=report.experiment,
                cost=report.cost,contexts=report.context)
            text=render_json(report) if ns.format=='json' else render_human(report)
            _stderr(f'{info.code}: {info.message}\n')
    try:
        _stdout(text)
    except Exception:
        _stderr('OUTPUT_FAILED: stdout delivery failed; any published artifact is retained.\n')
        return 130 if report.status=='cancelled' else 7
    except KeyboardInterrupt:
        _stderr('CANCELLED: stdout delivery interrupted.\n')
        return 130
    return report.exit_code
