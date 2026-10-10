"""A parse-only error boundary that never echoes untrusted arguments."""

import argparse
import inspect

from agentreducer._version import __version__


class ParseFailure(Exception):
    def __init__(self, *, format="human", domain=None):
        super().__init__("INVALID_ARGUMENTS")
        self.format = format
        self.domain = domain


class InformationalExit(Exception):
    def __init__(self, code=0):
        super().__init__("INFORMATION")
        self.code = code


class SafeArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["allow_abbrev"] = False
        if "color" in inspect.signature(argparse.ArgumentParser).parameters:
            kwargs["color"] = False
        super().__init__(*args, **kwargs)

    def error(self, message):
        raise ParseFailure()

    def exit(self, status=0, message=None):
        if status == 0:
            raise InformationalExit(0)
        raise ParseFailure()


def build_parser():
    placement="Root options precede the prompt, instructions, or repository subcommand."
    consent=("AMBIENT is uncontrolled and unfrozen. Reductions may launch multiple agent task processes; "
        "M5 adds per-classification trials. Trial and process budgets do not bound backend model requests.")
    root = SafeArgumentParser(prog="agentreducer", description="Reduce a reproduced failure in fresh workspaces.",
        epilog=placement+" "+consent)
    root.add_argument("--version", action="version", version=f"agentreducer {__version__}")
    root.add_argument("--format", choices=("human", "json"), default="human")
    output = root.add_mutually_exclusive_group()
    output.add_argument("--output", metavar="PATH", help="Atomically publish the report")
    output.add_argument("--reduced-output", metavar="PATH", help="Publish opt-in raw reduced artifact")
    root.add_argument("--force", action="store_true", help="Allow replacement of the chosen destination")
    root.add_argument("--debug", action="store_true", help="Sanitized stack frames on stderr")
    domains = root.add_subparsers(dest="domain", required=True, parser_class=SafeArgumentParser)
    for domain in ("prompt", "instructions", "repository"):
        leaf = domains.add_parser(domain,epilog=placement+" "+consent)
        leaf.add_argument("--source", required=True, help="Explicit source workspace template")
        leaf.add_argument("--oracle", choices=("command", "codex"), required=True)
        verify = leaf.add_mutually_exclusive_group(required=True)
        verify.add_argument("--verify-argv-json", metavar="JSON")
        verify.add_argument("--verify-argv-file", metavar="PATH")
        leaf.add_argument("--verify-timeout", default="60")
        leaf.add_argument("--pass-exit-code", action="append")
        leaf.add_argument("--fail-exit-code", action="append")
        leaf.add_argument("--exclude-basename", action="append", default=[])
        if domain != "repository":
            inp = leaf.add_mutually_exclusive_group(required=True)
            inp.add_argument("--input-file", metavar="PATH")
            inp.add_argument("--stdin", action="store_true")
            inp.add_argument("--text")
            leaf.add_argument("--candidate-path", default="AGENTS.md" if domain == "instructions" else None)
        if domain == "prompt":
            leaf.add_argument("--split", choices=("paragraph", "line"), default="paragraph")
        else:
            task = leaf.add_mutually_exclusive_group()
            task.add_argument("--task-file", metavar="PATH")
            task.add_argument("--task-stdin", action="store_true")
            task.add_argument("--task-text")
        if domain == "repository":
            leaf.add_argument("--pin", action="append", default=[])
            leaf.add_argument("--verifier-path", action="append", default=[])
        leaf.add_argument("--stochastic", action="store_true",help="Repeat each classification under an explicit trial policy and global trial budget")
        for option in ("min-trials", "max-trials", "failure-threshold", "max-total-trials"):
            leaf.add_argument("--" + option)
        leaf.add_argument("--allow-agent-execution", action="store_true",help="Opt in to repeated agent tasks that may incur backend model charges; inspect the qualifications below")
        leaf.add_argument("--model")
        leaf.add_argument("--context-mode", choices=("ambient", "controlled"),help="AMBIENT is uncontrolled and unfrozen; CONTROLLED currently fails closed")
        codex = leaf.add_mutually_exclusive_group()
        codex.add_argument("--codex-executable", metavar="PATH")
        codex.add_argument("--codex-argv-json", metavar="JSON")
        codex.add_argument("--codex-argv-file", metavar="PATH")
        leaf.add_argument("--agent-timeout")
    return root


def parse_cli(argv=None):
    ns = argparse.Namespace()
    try:
        return build_parser().parse_args(argv, namespace=ns)
    except ParseFailure:
        # Argparse parses subcommands into a child namespace, so the root's
        # valid subcommand is assigned only after a successful leaf parse.
        # Capture it through the subparser action, without inspecting raw argv.
        raise ParseFailure(format=getattr(ns, "format", "human"),
                           domain=getattr(ns, "domain", None)) from None
