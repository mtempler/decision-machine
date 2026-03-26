# Decision Machine — Data Dictionary

Version 1.0 · March 2026

---

## Input Time-Series CSV

SML-App accepts time-series data as CSV files uploaded through the Time-Series Views panel.

### Format Requirements

| Rule | Detail |
|------|--------|
| First column | Date in `YYYY-MM-DD` format — required on every data row |
| Remaining columns | Numeric time-series values — at least one required |
| Header row | Required — first row must contain column names |
| Minimum rows | At least one data row beyond the header |
| File type | `.csv` only — `.xlsx` and other formats rejected |
| Encoding | UTF-8 or UTF-8 BOM |

### Example

```
date,price,volume
2024-01-01,142.50,1200000
2024-01-02,143.75,980000
2024-01-03,141.20,1450000
```

### Validation Rules

SML-App validates the following on upload:

- Date column is present and all values match `YYYY-MM-DD`
- At least one additional numeric column is present
- File contains at least one data row

### Known Gaps

- **Missing values** — blank cells are not currently detected at upload time. A CSV with missing values will pass validation but may produce a pipeline error during SML processing.
- **Non-numeric data** — text in numeric columns is not validated at upload time.
- **Date gaps** — non-contiguous date sequences are not detected.

These validations are on the roadmap.

---

## Binary Output — OnDemand Binary Dynamics Analysis

Triggered by submitting a Binary job. The output CSV is served with an injected header row by SML-App.

**Purpose:** Machine learned measurement dynamics in a generally non-equilibrium setting. Any time-series of values can be equally well processed. Triggered using the `config_` prefix file.

### Output Columns

| Column | Description | Format |
|--------|-------------|--------|
| `Symbol` | Time-series identifier — derived from the input filename | String |
| `TS` | Timestamp — date of the measurement | YYYY-MM-DD |
| `value` | The raw measured value from the input time-series | Float, 4 d.p. |
| `p+` | Computed, machine learned probability that the value will go up in the next time step. Computed exactly up to machine error. The probability is a function of system energy and process extent (Lagrange multiplier). | Float, 4 d.p. |
| `p-` | Computed, machine learned probability that the value will go down or stay the same in the next time step. Computed exactly up to machine error. The probability is a function of system energy and process extent (Lagrange multiplier). | Float, 4 d.p. |
| `energy` | Computed, machine learned system energy measured from equilibrium as a zero offset. Positive: Bull. Negative: Bear. Internally generated energy could include emotions (plural) — human-generated displacement energy from equilibrium. Scientists often design experiments with constant energy or temperature; these systems are said to be in equilibrium. Equilibrium implies that the probability that an asset price will go up equals the probability that it won't go up (an "unbiased coin"). Markets are driven by emotions which throw the actions of market participants into non-equilibrium, almost all of the time. | Float, 4 d.p. |
| `power` | Rate of energy flow per time step. Combines Emotion and Resistance. At equilibrium, Power = Emotion² / R (V²/2R). Power is the energy flow per time step and calculates the power available to perform work. | Float, 4 d.p. |
| `resistance` | Market resistance to changing price. Wherever there is energy available to move the needle, there is also resistance. The more resistance, the harder it is to move the needle. Resistance is not constant in systems that are not in equilibrium. | Float, 4 d.p. |
| `noise` | Computed, machine learned market (Nyquist) noise that dissipates system energy so that it cannot be used for price movement. Power can be wasted through dissipation (strain or viscosity) making it unavailable to do work. As noise increases, the amount of wasted power increases. | Float, 4 d.p. |
| `T` | Entropic temperature of the system. In non-equilibrium dynamics, the free energy and temperature are coupled to produce a heat engine. By observing free energy and temperature together, price entry and exit points can be identified. Temperature is the reciprocal of the derivative of entropy with respect to energy — the general definition of entropic temperature. Recommend plotting free energy and temperature as a double-sided plot. | Alpha-numeric |
| `FE` | Helmholtz free energy. The total energy / emotion available to do useful work (F = E − TS, Helmholtz). Minimum Free Energy is a more convenient form of maximum entropy in non-equilibrium problems. When free energy decreases it does work in the dominant emotion (bull or bear). In non-equilibrium, free energy and temperature can play off each other to generate a heat engine that drives price movements. Local maxima in free energy indicate when energy is available for price movement (an entry point). As free energy decreases from the local maximum, price movement in the direction of the dominant emotion can be observed. After the price movement, a local minimum will develop, equivalent to maximum entropy. The stable minima signifies an exit point for the dominant emotion trade. | Float, 4 d.p. |
| `therm_p+` | Computed, machine learned probability that the value will go up in the next time step when in a thermal bath of temperature T_R. Where thermal probabilities dominate over p+ and p-, thermal probabilities drive price movement. The thermal probabilities depend on the temperature difference between the system temperature (above) and the reservoir temperature T_R. Decision Machine uses a default value for T_R at statistical equilibrium (T_R = e/4) but it can and should be set by the customer in the configuration file. A dissipative system is a thermodynamically open system operating out of, and often far from, statistical or thermal equilibrium, exchanging energy and information with its environment. | Float, 4 d.p. |
| `therm_p-` | Computed, machine learned probability that the value will not go up in the next time step when in a thermal bath of temperature T_R. See therm_p+ for full description. | Float, 4 d.p. |

---

## Units Output — OnDemand Units Analysis

Triggered by submitting a Units job. The output CSV is served with an injected header row by SML-App.

**Purpose:** Scientific measurements of time-series dynamics based on the science of counting — counting units of measure with constraints on maximum entropy.

> **Note:** The column definitions below represent the intended end state of the Units output. The current pipeline produces a superset of scientific measurements; the columns below reflect the target reporting set.

### Output Columns

| Column | Description | Format |
|--------|-------------|--------|
| `Symbol` | Time-series identifier — derived from the input filename | String |
| `TS` | Timestamp — date of the measurement | YYYY-MM-DD |
| `value` | The raw measured value from the input time-series | Float, 4 d.p. |
| `p` | Momentum. Measures the inertia of the time-series. As momentum increases, greater force is needed to produce a given deviation from the current direction. When displacement velocity vanishes (v = 0), momentum equals the mass (p = m). When E ≠ 0, p = m·exp(v). Note: momentum is non-linear and not equal to mv. Dispersion relations define the algebraic relationships between mass, momentum, and displacement energy measured from equilibrium. | Float, 4 d.p. |
| `E` | Displacement energy from equilibrium. The energy at equilibrium is subtracted from the total system energy to give the displacement energy. When E = 0, the system is in equilibrium — all states are equally likely, equivalent to no energy entering or exiting the system. When E ≠ 0, energy enters (E > 0) or exits (E < 0) the system. Non-zero displacement energy signals non-equilibrium behavior. Internally generated energy could include emotions — human-generated displacement energy from equilibrium. | Float, 4 d.p. |
| `T` | Entropic temperature of the system. | Float, 4 d.p. |
| `T_B` | Body temperature of the system. Same as the reservoir temperature. Thermodynamic expressions define and enforce algebraic relationships between energy E, free energy, temperature, free entropy, and body temperature through the second law of thermodynamics: ⟨F⟩ ≡ T⟨A⟩/T_B = E − TS, offset from equilibrium. When in thermal equilibrium, T = T_B and classical thermodynamics is regained. Equilibrium is not assumed and is infrequently observed in time-series. | Float, 4 d.p. |
| `exp_n` | Expected Supply, ⟨ξ⟩. Combines the expected count and the expected strain (energy stored in extension). Defined by the affine connection as part of Natural Dynamics. | Float, 4 d.p. |
| `exp_strain` | Expected strain component of the supply measurement. | Float, 4 d.p. |
| `exp_demand` | Expected Demand, ⟨η⟩. The expected value minus the expected strain — an extension that stores energy. Defined by the affine connection as part of Natural Dynamics. | Float, 4 d.p. |
| `sus_n` | Sustained supply count. | Float, 4 d.p. |
| `sus_strain` | Sustained strain component. | Float, 4 d.p. |
| `sus_E` | Sustained displacement energy. | Float, 4 d.p. |
| `sus_demand` | Sustained demand. | Float, 4 d.p. |
| `var_n` | Variance of supply count. | Float, 4 d.p. |
| `var_strain` | Variance of strain. | Float, 4 d.p. |
| `var_E` | Variance of displacement energy. | Float, 4 d.p. |
| `var_del_n` | Variance of change in supply count. | Float, 4 d.p. |
| `cov_n_strain` | Covariance of supply count and strain. | Float, 4 d.p. |

### Thermodynamic Relationships

The Units output is governed by the following relationships:

- **Free entropy:** ⟨M⟩ = S − E/T (Massieu free entropy). The slope of the free entropy equals −F, the force due to the potential.
- **Free energy:** ⟨F⟩ ≡ T⟨A⟩/T_B = E − TS, offset from equilibrium
- **Entropy:** S(λ,E) — the number of different counting configurations possible when subjected to the constraints of counting the time-series. A function of the Lagrange multiplier λ and displacement energy E.

---

## Related Documentation

- [Architecture](onboarding-architecture.md) — onboarding pipeline
- [Contributing](../README.md#contributing) — how to build agents on top of Decision Machine
- [SML-App source](https://github.com/mtempler/decision-machine)
