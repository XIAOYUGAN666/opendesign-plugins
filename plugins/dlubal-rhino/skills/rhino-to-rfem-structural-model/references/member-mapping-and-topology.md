# Member mapping and topology

Record geometry representation, an open-ended engineering role and the RFEM analysis type independently. A neutral or user-defined role is acceptable when analysis properties are established. A layer, color, material appearance or role name is evidence only; it cannot establish stiffness, material grade, release, prestress or connection.

For every member record source identities, geometric and analysis axes, material, section, local orientation, target line/member, start/end order and relevant connection properties. Distinguish measured values, user-provided facts and assumptions. Resolve installed SDK classes, fields and enums using `inspect_rfem_api`, rather than copying a fixed schema or guessing library identifiers. Confirm candidate material/section matches and explain differences.

Find candidate connections geometrically, then decide them using confirmed engineering intent. Distinguish endpoint contact, endpoint-to-interior, multi-member junctions, offsets, crossings without connection and continuous members split into segments. Preserve offsets and physical-member continuity; proximity or coincidence does not imply a rigid joint. Do not merge parallel members or create releases just because a member is split. Missing required properties remain unresolved rather than silently defaulted.
