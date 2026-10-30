# Axis and section extraction

Choose the extraction method by actual geometry, not a presumed structural system. Keep the original geometric axis and proposed analysis axis separately, including source identity, end points, curve definition, section frame, method, evidence, residuals and unresolved items.

- For an existing analysis curve, inspect the full curve and its intended use. When no solid exists, obtain section and frame from attributes or user input; never claim they were measured from an absent solid.
- For a straight extrusion, inspect the extrusion path and actual cross-section; adjust the path to the section centroid when necessary, accounting for holes and transformations.
- For cut or notched solids, inspect multiple interior sections, fit an axis from suitable centroids and report errors. Separate the physical cut ends from proposed analysis nodes.
- For curved members, preserve the verified guide or centroid trajectory and complete curve parameters. Do not substitute a chord or bounding-box axis. Any approximation and error require explicit confirmation.
- For blocks, retain instance and nested paths, apply accumulated transformations and check mirrored or nonuniformly scaled section dimensions and local frames. Do not explode the source document.
- For variable, composite, fused, mesh or other geometry, inspect available evidence. A geometric centroid is not automatically the required analysis reference axis. Mark an individual object unresolved when the current tools cannot justify its axis or section.

Verify section orientation at suitable positions, including width/height, holes, rotation and symmetry ambiguity. Reversing member direction also changes start/end-dependent releases, offsets and local-frame interpretation. Present every proposed extension, shortening or idealization for review.
