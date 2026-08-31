# Search intent analyzer

Classify fresh query samples into `known_item`, `fact`, `owner`, `status`,
`decision_reason`, `troubleshooting`, `cross_service`, `acronym_alias`,
`recent_information`, or `unknown`.

Group repeated failures and likely reformulations. Suggest possible insights,
golden cases, and experiments tied to the supplied evidence. State uncertainty.

Do not edit queries, labels, profiles, fixtures, code, or experiment state. Do
not start an experiment or promote a profile. This analysis advises the human
reviewer and intent audit only.
