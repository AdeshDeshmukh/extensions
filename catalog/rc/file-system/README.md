# File System extension

This experimental extension describes a file system path that a workload
expects to be mounted, such as a data directory, a scratch directory or a
directory of configuration files. A Condition states what the workload expects
at the path. It does not name the storage technology behind it; NFS, a cloud
file service or a local disk can all fulfill the same Condition.

The first immutable candidate release is
[`0.1.0`](releases/0.1.0/runtimeconditions.extension.yaml).

Example Condition:

```yaml
- name: library-data
  kind: rc.file_system
  interface:
    type: mount
    path: /var/lib/media-library
    access: read_write
    persistence: persistent
    minimumSize: 10Gi
```

A workload that reads the mount path from an environment variable declares the
variable instead of, or in addition to, a literal path:

```yaml
- name: credentials
  kind: rc.file_system
  interface:
    type: mount
    access: read_only
  configuration:
    env:
      - property: path
        name: CREDENTIALS_DIR
```

A complete Profile with a data directory, a read-only configuration mount and
an environment-supplied path is in
[`examples/data-and-config-mounts-profile.yaml`](examples/data-and-config-mounts-profile.yaml).

## Vocabulary

| Field | Values | Adapter decision it enables |
| --- | --- | --- |
| `interface.path` | Absolute path | Where to mount. Required unless `configuration` supplies the path. |
| `interface.access` | `read_only`, `read_write` | Whether the mount must be writable. Required. |
| `interface.persistence` | `persistent`, `ephemeral` | Whether written data must survive a restart or replacement of the workload instance (`persistent`) or may be discarded with it (`ephemeral`). Required when `access` is `read_write`. |
| `interface.sharing` | `shared`, `exclusive` | Whether every instance of the workload must see the same contents (`shared`) or each must have its own (`exclusive`). Optional; omitted means no requirement. |
| `interface.minimumSize` | Whole number with `Mi`, `Gi` or `Ti`, such as `10Gi` | The least capacity to provide. Optional. |
| `configuration.env[].property` | `path` | Which environment variable must hold the mount path, so the adapter can choose where to mount and tell the workload. |

A Condition gives the path as `interface.path`, through `configuration`, or
both. With both, the literal is the workload's default and the variable
overrides it.

`persistence`, `sharing` and `minimumSize` describe data the workload writes.
Omit them on a read-only mount, whose contents the target environment supplies.

The contents of a read-only mount, file ownership and permissions, performance
and encryption are intentionally not part of this release.
