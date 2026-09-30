# Model Validation and Calibration

## Purpose

This validation evaluates whether the prototype behaves within selected
engineering plausibility ranges and whether expected cause-and-effect
relationships are preserved.

The validation is not intended to claim operational equivalence with Maitri
or Bharati because the project does not have access to the stations' complete
operational telemetry dataset.

## Data Classification

### External / Published Inputs

These inputs are based on publicly available information from sources such as
the Ministry of Earth Sciences (MoES), National Centre for Polar and Ocean
Research (NCPOR), manufacturer specifications, and published Antarctic
climatology and logistics information.

Examples include:

- Station location and basic metadata
- Reported crew complements
- Reference generator specifications
- Antarctic environmental ranges
- Publicly reported logistics information

### Engineering Assumptions

Some station-specific engineering parameters are not publicly available.
These are explicitly marked as assumptions in `params.yaml`.

Examples include:

- Building thermal parameters
- Thermal mass approximation
- Renewable system sizing
- Battery capacity
- Generator MTBF
- Fault probability
- Storm arrival characteristics
- Snow-melt plant capacity

### Synthetic Data

The prototype telemetry service generates synthetic environmental and
equipment telemetry for demonstration and testing.

This is intentional because complete operational Maitri/Bharati telemetry is
not publicly available to the project team.

## Validation Tests

The prototype evaluates:

- State transition correctness
- Energy dispatch consistency
- Renewable generation behavior
- Battery operating limits
- Generator fault behavior
- Thermal response
- Consumable depletion
- Scenario behavior
- Forecast behavior
- Sensitivity response
- Deterministic replay
- RBAC permissions
- Audit logging

## Interpretation

Passing these checks demonstrates consistency with the implemented model and
selected engineering plausibility ranges.

These checks should not be interpreted as field certification, operational
validation, or a claim that the prototype reproduces the complete physical
behavior of the deployed Bharati or Maitri stations.

## Parameter Provenance

Parameter-level source and assumption information is maintained in:

`params.yaml`

The Streamlit dashboard exposes this information through:

**Provenance & Audit → Parameter Provenance**
