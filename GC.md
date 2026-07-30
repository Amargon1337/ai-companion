# Memory Garbage Collection

GC is intentionally conservative and reversible at the record level. A candidate must be an old, low-importance, unused, unlinked event with no protected tags or lifecycle locks.

`/memory_gc` performs a dry-run. `/memory_gc apply` archives candidates and removes their embeddings. It never hard-deletes facts or relations. Automatic nightly health reporting is enabled; automatic GC application is not.
