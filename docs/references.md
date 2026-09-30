# References and Data Provenance

This project combines publicly available information, engineering assumptions,
and synthetic simulation data.

## Data Classification

### External / Published Information

These inputs are based on publicly available information from sources such as
the Ministry of Earth Sciences (MoES), National Centre for Polar and Ocean
Research (NCPOR), manufacturer specifications, and published Antarctic
climatology/logistics information.

Examples include:

- Station location and basic station metadata
- Reported crew complements
- Reference generator specifications
- Antarctic environmental ranges
- Publicly reported logistics/fuel information

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

## Model Validation

The validation process checks:

- State transition correctness
- Energy dispatch consistency
- Battery operating limits
- Generator fault behavior
- Thermal response
- Consumable depletion
- Scenario behavior
- Forecast behavior
- Sensitivity response
- Deterministic replay
- RBAC and audit functionality

Passing these checks demonstrates consistency with the implemented model and
selected engineering plausibility ranges. It does not constitute field
certification or operational validation of the physical Bharati or Maitri
stations.

## Parameter-Level Provenance

The complete parameter-level source/assumption information is maintained in:

`params.yaml`

The Streamlit dashboard exposes the same information through:

**Provenance & Audit → Parameter Provenance**