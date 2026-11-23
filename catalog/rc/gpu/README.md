# GPU requirement extension

This first candidate release lets a workload declare that it needs one or more
whole GPU devices. `interface.count` is the minimum number of devices.
`interface.minMemoryMiB` is the minimum memory **per device**, in mebibytes
(1 MiB = 1,048,576 bytes). An adapter can use these requirements to select a
compatible node or report that the platform cannot fulfill the workload.
`interface.vendor` is optional. Set it when the workload requires NVIDIA, AMD,
or Intel GPU hardware; leave it out when any compatible GPU may fulfill the
requirement.

The definition is [`0.1.0`](releases/0.1.0/runtimeconditions.extension.yaml).
The [inference example](examples/inference-profile.yaml) asks for one NVIDIA GPU
with at least 16 GiB of memory, as a CUDA-based image might require.

The vendor field constrains hardware selection; it does not guarantee a
compatible driver or CUDA version. This release does not express driver,
library-version, or partitioning requirements. `count` means whole devices, not
fractional or partitioned allocation. It also does not prescribe Kubernetes
resource names, node labels, or provisioning technology. Those decisions belong
to the fulfilling platform.
