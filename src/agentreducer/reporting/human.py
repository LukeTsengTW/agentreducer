"""Plain-text view of the same privacy-safe report."""

from agentreducer._version import __version__


def _count(value):
    return str(value.value) if value.value is not None else f"unknown (at least {value.lower_bound})"


def render_human(report):
    lines=[f"AgentReducer {__version__}",f"Domain: {report.domain or 'unavailable'}"]
    if report.status=="reduced":
        outcome="policy-relative failure reproduced" if report.context["stochastic"] else "configured-oracle failure reproduced"
        lines.append(f"Result: {outcome}")
        result=report.result
        if report.domain=="repository":
            lines.extend((f"Reducible files: {result['original_file_count']} -> {result['reduced_file_count']}",
                f"Pinned files: {result['pinned_file_count']}",
                f"File reduction: {result['file_reduction_percentage']:.2f}%",
                f"Bytes: {result['original_bytes']} -> {result['reduced_bytes']} ({result['byte_reduction_percentage']:.2f}% removed)"))
            lines.extend("Retained: "+x['path'] for x in result['retained'])
            lines.extend("Pinned: "+p for p in result['pinned_paths'])
        else:
            lines.extend((f"Blocks: {result['original_block_count']} -> {result['reduced_block_count']}",
                f"Characters: {result['original_character_count']} -> {result['reduced_character_count']}",
                f"Character reduction: {result['character_reduction_percentage']:.2f}%",
                f"Bytes: {result['original_bytes']} -> {result['reduced_bytes']}"))
            lines.extend(f"Retained block: {x['original_index']} sha256={x['sha256']}" for x in result['retained'])
    else:
        lines.append(f"Result: {report.status}")
    cost=report.cost
    lines.extend((f"Oracle evaluations: {cost.logical_evaluations}",
        f"Agent process launches: {_count(cost.agent_launches)}",f"Verifier process launches: {_count(cost.verifier_launches)}"))
    if cost.underlying_trial_attempts is not None:
        lines.append(f"Underlying trial attempts: {cost.underlying_trial_attempts}")
    if cost.projection_attempts is not None:
        lines.append(f"Projection attempts/completed checks: {cost.projection_attempts}/{cost.completed_projection_checks}")
    if cost.budget is not None:
        lines.append(f"Trial budget: {cost.budget['used']}/{cost.budget['limit']}")
    if report.context['stochastic']:
        p=report.context['stochastic']['policy']; f=p['failure_threshold']
        lines.append(f"Policy: {p['min_trials']}..{p['max_trials']} trials, threshold {f['numerator']}/{f['denominator']}")
    visibility=report.context['repository_visibility']
    if visibility:
        lines.extend(("Workspace projection: "+("verified" if visibility['workspace_projection_verified'] else "not established"),
            "External read isolation: "+visibility['external_read_isolation'].replace('-',' ')))
    if report.error:
        lines.append(f"Error [{report.error['code']}]: {report.error['message']}")
    lines.extend("Limitation: "+s['message'] for s in report.limitations)
    return '\n'.join(lines)+'\n'
