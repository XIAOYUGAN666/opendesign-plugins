# Validation and reporting

## Base-model validation

Check the task-relevant subset of:

- node, line, member, member-set, and surface connectivity;
- supports, releases, degrees of freedom, and unintended mechanisms;
- materials, sections, thicknesses, local axes, units, and references;
- load cases, load directions, magnitudes, combinations, and design situations;
- enabled add-ons and objects selected for design.

## Structural-analysis validation

Confirm:

- calculation completed and relevant warnings/errors were captured;
- reactions and applied loads are broadly consistent;
- displacement sign, magnitude, and units are plausible;
- zero stiffness, singularity, mechanism, unconstrained DOF, and unit mistakes are absent or explained;
- the reported combination/design situation matches the result.

## Timber Design validation

For the task-relevant member, member-set, or surface scope, confirm:

- Timber Design is enabled through installed API fields;
- materials, sections/thicknesses, service conditions, effective lengths, and required configurations are assigned;
- applicable ULS, SLS, stability, and deformation checks were requested;
- the governing object, location, combination/design situation, utilization or displacement can be identified;
- RFEM-supported checks and Chinese-standard evidence are mapped explicitly;
- requirements not represented by RFEM remain listed as manual or incomplete checks.

RFEM calculation success is not a compliance result. A compliance statement requires verified evidence, a validated model, successful calculation, and validated design output.

## Stage report

After each stage, report:

- actual objects and IDs read/created/modified;
- postconditions that passed or failed;
- calculation status;
- warnings, partial mutation, and differences from the plan;
- whether the next stage is safe to propose.

## Final report

Separate:

1. user inputs;
2. verified RFEM model facts;
3. validated `[E<n>]` knowledge-base standard evidence;
4. main-Agent engineering derivations and RFEM mappings;
5. assumptions;
6. warnings, failures, and blocked/incomplete work.

When available, report governing member/surface, location, combination or design situation, utilization, displacement, and controlling check. State the exact verified scope and do not claim professional sign-off, legal approval, or compliance beyond that scope.

For an analysis that fails, success means the failure is accurately located, evidenced, and reported—not that the structure is forced to pass.
