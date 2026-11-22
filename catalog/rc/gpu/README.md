# GPU requirement extension

This first candidate release lets a workload declare that it needs one or more
whole GPU devices. `interface.count` is the minimum number of devices.
`interface.minMemoryMiB` is the minimum memory **per device**, in mebibytes
(1 MiB = 1,048,576 bytes). An adapter can use these requirements to select a
compatible node or report that the platform cannot fulfill the workload.

The definition is [`0.1.0`](releases/0.1.0/runtimeconditions.extension.yaml).
The [inference example](examples/inference-profile.yaml) asks for one GPU with
at least 16 GiB of memory.

The release deliberately does not specify a GPU vendor, driver, CUDA version,
or partitioning mode. Those choices need portable, adapter-actionable semantics
before they become immutable extension vocabulary. `count` means whole devices;
this release does not describe fractional or partitioned allocation. It also
does not prescribe Kubernetes resource names, node labels, or provisioning
technology. Those decisions belong to the fulfilling platform.
