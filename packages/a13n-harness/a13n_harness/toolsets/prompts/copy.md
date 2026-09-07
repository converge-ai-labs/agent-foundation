## Best practices

- Use explicit source and destination pairs and review broad batches before copying them.
- Use `copy` when data must cross Environment mounts; shell commands operate within one selected mount.
- Enable overwrite only when replacement is intentional.
