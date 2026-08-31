# Migration hardening backlog

These items do not block the Feature 2–12 migration. Revisit them after
Feature 12 if they still matter.

- Improve compatibility diagnostics when a supported table exists but a join
  key such as `permission_sets.id` has an unexpected type or is missing.
