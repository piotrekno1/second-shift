# Control

[README](../README.md) · [Security model](security-model.md) · [Configure](server-configuration.md)

What a job can reach, call and spend is set in four places:

- **[Access](access.md)**: workspaces, who can approve, run, schedule and
  view, and what API tokens can do. Set by workspace owners.
- **[Services](services.md)**: which tools a job may call, and with which
  arguments. Set by workspace owners.
- **[Spend](spend.md)**: model calls, tool calls, tokens and time per run,
  runs per hour and per day, and which models. Set by owners and admins,
  within the server's maximums.
- **[Sandbox and compute](sandbox.md)**: what a run's commands can reach, and
  the memory, CPU and images they get. Set by the server operator.

Everything here is set in the dashboard or on the server.
`.secondshift.yaml` can ask for access, but it cannot grant itself access,
pick a credential, change a limit or touch the sandbox. A change applies from
the next run. A run keeps the plan it started with.

## Before you schedule a job

1. Connect services with the narrowest credential they offer.
2. Allow only the tools jobs need on each connection.
3. Turn on [Require approval for each job](services.md#require-approval-for-each-job)
   for connections that can change data.
4. [Restrict the arguments](services.md#restrict-arguments) that pick a target,
   such as a project or channel.
5. Run the job once with **Run now**. Read its timeline and its usage against
   its [limits](spend.md#run-limits), and adjust them.
6. Then turn on its schedule or trigger.

For the threat model behind these controls, and the risks they leave, see the
[security model](security-model.md#remaining-risks).
