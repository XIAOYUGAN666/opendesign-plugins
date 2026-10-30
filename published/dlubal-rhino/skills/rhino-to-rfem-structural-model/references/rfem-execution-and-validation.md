# RFEM execution and validation

Verify the actual MCP declarations and engineering connection. `inspect_rfem_api` describes installed SDK classes, fields, enums and methods; it does not describe MCP input schemas. Inspect uncertain SDK details before constructing objects. Use SI units and verify the coordinate transformation and global axis direction.

After explicit proposal confirmation and the existing tool authorization, default to a dedicated new model. Record its actual GUID and pass it explicitly in subsequent stages. Create materials and sections, nodes and lines, members and confirmed connection properties in verified dependency order. The bounded `write_rfem_model` creates a new model and accepts at most 1000 objects; do not call it repeatedly as an append operation. Use the existing bound `run_rfem_script` for necessary staged work, subject to its restrictions. Track each execution ID and postconditions.

A failure may leave partial writes. On timeout or unknown, query `get_rfem_execution` for the original ID and do not replay; while busy or unknown, do not send competing writes or assume a safe read. Once settled, compare actual partial state and propose any repair for separate confirmation. Do not clear or overwrite models to recover.

After resolving execution state, select recovery from actual postconditions and the confirmed target strategy. Do not automatically require a new model or a complete restart for every failure. Preserve already verified work, and obtain a new confirmation only for changes to the accepted plan.

Read back every target object and compare its source mapping, geometry, units, curve, section, material, local axes, connections, offsets, releases and support to the confirmed proposal. Distinguish legitimate free ends from unintended isolated components. Report discrepancies, incomplete work and save status. A successful MCP response is not proof of a correct engineering model; calculation and design checks are not performed by conversion alone.
