# Corporate PPA structuring and shaping risk on Polish power prices

[![tests](https://github.com/kwwaleszczynski-del/pl-cppa-shaping-risk/actions/workflows/ci.yml/badge.svg)](https://github.com/kwwaleszczynski-del/pl-cppa-shaping-risk/actions/workflows/ci.yml)

When an industrial electricity buyer in Poland signs a corporate power purchase agreement (cPPA), the first decision
is who holds the profile risk between variable renewable output and the factory's consumption schedule. A
**pay-as-produced (PaP)** contract settles on the generator's meter and leaves the buyer exposed to solar midday
cannibalisation and evening deficits; a **profiled or baseload CfD** fixes the buyer's hourly hedge on a target load
schedule (`100 GWh/yr`, `11.42 MW` flat or two-shift) and transfers the shaping, imbalance and weather-price
covariance risk to the seller or sleeving utility. This project quantifies and decomposes those risks on Polish power
prices:

1. **Polish spot and capture price structure** on an 8,760-hour reference dataset calibrated to TGE Day-Ahead / PSE
   SDAC market characteristics (`435.0 PLN/MWh` time-weighted mean spot price, `0.64` solar capture factor, `0.94`
   onshore wind capture factor, `354 hours/year` of negative or zero prices) and a **500-path seasonal
   block-bootstrap Monte Carlo engine** that preserves synoptic Dunkelflaute episodes and multi-year weather-price
   covariance.
2. **Exact four-term settlement decomposition** between a fixed-price PPA (`Strike S = 380 PLN/MWh`) and physical
   renewable generation across three `100 GWh/yr` industrial load profiles (`Flat Baseload 24/7`, `Two-Shift
   Industrial 06:00-22:00 Mon-Sat`, and `Cold-Storage / Food Processing`), verified to `< 1e-9 PLN/MWh` in every
   simulated year.
3. **Negative-price floor clauses** (`Unfloored two-way CfD`, `Zero-price strike floor: no CfD payout when P_spot < 0`,
   and the `6-hour rule` EU CfD style), quantifying the exact fair strike uplift (`+28.0 to +44.1 PLN/MWh` for pure
   PV, `+9.0 to +14.1 PLN/MWh` for onshore wind) required when the buyer suspends settlement during negative spot
   hours.
4. **Portfolio sizing (`PV + Onshore Wind + 2h/4h BESS`)** across `0 %` to `100 %` wind shares and co-located battery
   durations (`0 h`, `2 h`, `4 h`) to minimise shaping risk cost, maximise physical 24/7 hourly Carbon-Free Energy
   matching (`CFE 24/7 %`), and protect P90 conservative annual cashflow.
5. **Verification against analytical ground truth** on harmonic diurnal price-generation processes and bivariate
   Gaussian volume-price covariance processes, confirming exact accounting and estimator recovery.

Interactive dashboard: https://kwwaleszczynski-del.github.io/pl-cppa-shaping-risk/

> Every contract term, strike price, negative-price floor formula and industrial load profile in this repository is an
> **illustrative engineering benchmark** calibrated to Polish market statistics, not a reading of any specific contract
> or financial advice.

## Key results

### 1. Hybrid PV + wind + battery sizing cuts baseload shaping cost from 142 to 11 PLN/MWh

Converting a `100 GWh/yr` standalone solar PV profile into a flat `11.42 MW` baseload PPA on Polish spot prices incurs
a shaping risk cost of **142.0 PLN/MWh** (`37.4 %` of a `380 PLN/MWh` strike): the seller sells midday surpluses into
cannibalised summer troughs (`278.4 PLN/MWh` raw capture price, `0.64` capture factor) and buys back night and winter
evening deficits at thermal peak prices. Combining onshore wind with solar PV and co-located storage transforms both
the financial shaping cost and physical 24/7 hourly matching:

| Portfolio (`100 GWh/yr` generated vs `100 GWh/yr` flat baseload PPA at `S = 380 PLN/MWh`) | Raw capture price (`PLN/MWh`) | Shaping risk cost (`PLN/MWh`) | Seller P50 net revenue (`PLN/MWh`) | Seller P90 conservative (`PLN/MWh`) | Physical 24/7 hourly CFE (`%`) | Unhedged deficit bought on spot (`GWh/yr`) |
|---|---|---|---|---|---|---|
| `100 % PV`, `0 h BESS` | 278.4 | 142.0 | 238.4 | 213.4 | 44.0 % | 56.0 |
| `100 % PV`, `+ 2 h BESS` (`16.5 MW / 33 MWh`) | 278.4 | 96.0 | 284.3 | 264.8 | 61.5 % | 38.5 |
| `50 % Wind + 50 % PV`, `0 h BESS` | 343.7 | 40.3 | 340.0 | 310.1 | 71.5 % | 29.8 |
| `65 % Wind + 35 % PV`, `0 h BESS` | 363.2 | 31.0 | 349.4 | 317.3 | 73.2 % | 26.8 |
| `65 % Wind + 35 % PV`, `+ 2 h BESS` (`16.5 MW / 33 MWh`) | 363.2 | 11.0 | 369.3 | 344.2 | 81.0 % | 19.0 |
| `65 % Wind + 35 % PV`, `+ 4 h BESS` (`16.5 MW / 66 MWh`) | 363.2 | 4.2 | 376.1 | 355.5 | 86.4 % | 13.6 |
| `100 % Wind`, `0 h BESS` | 408.9 | 48.5 | 334.6 | 293.7 | 64.8 % | 35.2 |

- **Complementarity between Polish winter wind and summer PV drives the interior optimum at 65 % wind / 35 % PV.**
  Pure onshore wind has a much higher raw capture factor (`0.94`, `408.9 PLN/MWh`) than solar PV (`0.64`,
  `278.4 PLN/MWh`), yet a `100 % Wind` baseload PPA still carries **48.5 PLN/MWh** of shaping and tail risk because
  summer wind lulls and multi-day winter Dunkelflaute episodes force the seller to buy back `35.2 GWh/yr` of deficit
  power during high-price hours. Blending `35 % PV` with `65 % Wind` fills the daytime summer gap, cutting unhedged
  deficits to `26.8 GWh/yr` and lowering shaping cost to **31.0 PLN/MWh**.
- **A co-located 2 h BESS bridges the remaining diurnal gap.** Adding a `16.5 MW / 33 MWh` (`2 h`) battery to the
  `65 % Wind + 35 % PV` portfolio absorbs negative-price midday solar surpluses and discharges into the `17:00-21:00`
  evening ramp, cutting the shaping risk cost to **11.0 PLN/MWh** and lifting physical 24/7 hourly CFE matching from
  **44.0 %** (pure PV) and **73.2 %** (unbuffered hybrid) to **81.0 %** (**86.4 %** with a `4 h` battery).
- **Buyer load profile shifts the optimal renewable mix.** Replacing the flat `24/7` baseload buyer with a
  `Two-Shift Industrial` buyer (`06:00-22:00 Mon-Sat`, `446.8 PLN/MWh` load-weighted spot price) or a
  `Cold-Storage / Food Processing` buyer (`412.5 PLN/MWh` load-weighted spot price, peaking on warm summer afternoons)
  reduces pure-PV shaping cost by `18.5 PLN/MWh` and `29.0 PLN/MWh` respectively, shifting the cost-minimising wind
  share from `65 %` down to `55 %` for two-shift manufacturing and `45 %` for cold storage.

![shaping cost mix](docs/img/shaping_cost_mix.png)

### 2. Four-term settlement decomposition and multi-year weather-price covariance

Across the 500 block-bootstrap weather-price years, the net cashflow gap between physical generation revenue and a
fixed `380.0 PLN/MWh` contract decomposes into four exact additive components (`Base Price Level`, `Profile / Shape
Risk`, `Volume Risk`, and `Cross-Term / Weather-Price Covariance`):

| Contract / Portfolio (`100 GWh/yr`, `S = 380 PLN/MWh`, unfloored reference) | Base price level $\bar{P} - S$ (`PLN/MWh`) | Profile / shape risk $P_{\text{cap}} - \bar{P}$ (`PLN/MWh`) | Multi-year volume + covariance (`PLN/MWh`) | Net seller settlement P50 / P90 (`PLN/MWh`) | Max accounting bridge error (`PLN/MWh`) |
|---|---|---|---|---|---|
| `Pay-as-Produced 100 % PV` (zero-price floor) | hedged on $E_{\text{gen}}$ | 0.0 (buyer holds shape) | -33.6 (floor clawback) | 346.4 / 326.8 | `< 1e-12` |
| `Pay-as-Produced 100 % Wind` (zero-price floor) | hedged on $E_{\text{gen}}$ | 0.0 (buyer holds shape) | -12.6 (floor clawback) | 367.4 / 335.2 | `< 1e-12` |
| `Baseload 100 % PV`, `0 h BESS` | hedged on $E_{\text{contract}}$ | -156.6 raw (-118.0 net) | -24.0 (cov + balancing) | 238.4 / 213.4 | `< 1e-12` |
| `Baseload 65 % Wind + 35 % PV`, `0 h BESS` | hedged on $E_{\text{contract}}$ | -71.8 raw (-21.5 net) | -9.5 (cov + balancing) | 349.4 / 317.3 | `< 1e-12` |
| `Baseload 65 % Wind + 35 % PV`, `+ 2 h BESS` | hedged on $E_{\text{contract}}$ | -7.5 after BESS shift | -3.5 (cov + balancing) | 369.3 / 344.2 | `< 1e-12` |

- **Multi-year volume-price covariance is systematically negative.** Years with below-average wind and solar output
  coincide with higher thermal dispatch and higher Polish spot prices ($\mathrm{Cov}(E_{\text{gen}}, P_{\text{cap}} - \bar{P}) < 0$).
  A seller short of volume in a low-wind year buys back the shortfall against the fixed `100 GWh/yr` baseload
  commitment at elevated spot prices, creating an asymmetric left tail visible in the P90 cashflow spread.
- **Pay-as-Produced does not eliminate shaping cost; it transfers it to the buyer.** Under a `Pay-as-Produced` PV
  contract at `S = 380 PLN/MWh`, the industrial buyer pays `380 PLN/MWh` for power worth `278.4 PLN/MWh` on the spot
  market and still has to procure `56.0 GWh/yr` of night and winter load at `486.2 PLN/MWh` deficit-weighted spot
  prices.

![settlement waterfall](docs/img/settlement_waterfall.png)

### 3. Negative-price floor clauses require +28 to +44 PLN/MWh strike uplift for PV

In the calibrated 2026 Polish market (`354 hours/year` with $P_{\text{spot}} \le 0$, of which `276 hours` occur in
consecutive runs of six hours or longer on spring/summer weekends and holidays), corporate buyers increasingly demand
negative-price protection so they never pay a CfD top-up above the fixed strike when spot prices turn negative. When
settlement is suspended during negative spot hours and the generator curtails output to avoid negative spot exposure,
the seller loses the fixed strike on curtailed volume and requires a higher strike on the remaining settled hours to
break even at the same annual revenue:

| Negative-price clause & PV penetration scenario (`Base Strike S = 380 PLN/MWh`) | `100 % PV` curtailed energy share (`%`) | `100 % PV` required strike uplift (`PLN/MWh`) | `65 % Wind + 35 % PV` required strike uplift (`PLN/MWh`) | `100 % Wind` curtailed energy share (`%`) | `100 % Wind` required strike uplift (`PLN/MWh`) |
|---|---|---|---|---|---|
| `2026 Baseline (354 neg h/yr)` — `Unfloored two-way CfD` | 0.00 % | +0.00 | +0.00 | 0.00 % | +0.00 |
| `2026 Baseline (354 neg h/yr)` — `6-hour rule (EU CfD style)` | 6.86 % | **+28.00** | +16.12 | 2.31 % | **+9.00** |
| `2026 Baseline (354 neg h/yr)` — `Zero-price floor (P_spot < 0)` | 8.84 % | **+36.84** | +21.02 | 3.31 % | **+13.01** |
| `2028 Moderate PV (440 neg h/yr)` — `6-hour rule` | 7.51 % | +30.86 | +17.44 | 2.42 % | +9.41 |
| `2028 Moderate PV (440 neg h/yr)` — `Zero-price floor (P_spot < 0)` | 9.68 % | +40.73 | +22.74 | 3.46 % | +13.61 |
| `2030 High PV (540 neg h/yr)` — `6-hour rule` | 8.07 % | +33.35 | +18.54 | 2.50 % | +9.73 |
| `2030 High PV (540 neg h/yr)` — `Zero-price floor (P_spot < 0)` | 10.39 % | **+44.08** | +24.16 | 3.57 % | **+14.09** |

- **The gap between the 1-hour zero-price floor and the 6-hour rule is driven by short midday solar troughs.** Roughly
  a quarter of Polish negative-price PV generation occurs in `2-5 hour` midday troughs (`11:00-14:00`) that do not
  trigger a 6-consecutive-hour threshold. Moving from a strict `P_spot < 0` zero-price floor to a `6-hour rule` cuts
  the required PV strike uplift in 2026 from **+36.84 PLN/MWh** down to **+28.00 PLN/MWh** (and from **+13.01** to
  **+9.00 PLN/MWh** for onshore wind).
- **Co-located storage monetises negative-price hours instead of curtailing them.** Adding a `2 h BESS` absorbs `58 %`
  of the hybrid portfolio's negative-hour surplus into battery charge and re-injects it during positive-price afternoon
  ramps, cutting the zero-floor strike uplift of the `65/35` hybrid from `+21.02 PLN/MWh` to `+8.60 PLN/MWh`.

![negative price clauses](docs/img/negative_price_clauses.png)

### 4. Cashflow distribution across 500 bootstrap years and hourly hedge dynamics

Across 500 seasonal block-bootstrap years (`100 GWh/yr` Baseload PPA at `S = 380 PLN/MWh`, target unfloored revenue
`38.00 M PLN/yr`), a standalone solar baseload seller faces severe downside dispersion (`P50 = 23.84 M PLN/yr`,
`P90 conservative = 21.34 M PLN/yr`, `CVaR_10 = 20.12 M PLN/yr`), whereas the `65 % Wind + 35 % PV + 2 h BESS`
portfolio concentrates annual cashflow tightly around the contract target (`P50 = 36.93 M PLN/yr`,
`P90 conservative = 34.42 M PLN/yr`, `CVaR_10 = 33.48 M PLN/yr`):

![cashflow distribution](docs/img/cashflow_distribution.png)

## Method

### Exact four-term settlement decomposition

Let $P_t$ denote the hourly spot price in hour $t \in \{1, \dots, T\}$ ($T = 8760$) with time-weighted annual mean
$\bar{P} = \frac{1}{T}\sum_{t=1}^T P_t$, $g_t$ the hourly renewable generation with annual volume
$E_{\text{gen}} = \sum_{t=1}^T g_t$ and generation-weighted capture price
$P_{\text{cap}} = \frac{1}{E_{\text{gen}}}\sum_{t=1}^T g_t P_t$, and $L_t$ the contracted buyer load schedule with
annual volume $E_{\text{contract}} = \sum_{t=1}^T L_t$ and load-weighted price
$P_{\text{load}} = \frac{1}{E_{\text{contract}}}\sum_{t=1}^T L_t P_t$. Under a fixed-price PPA at strike $S$, the
annual settlement gap per contracted MWh decomposes identically into four additive terms:

```math
\frac{E_{\text{gen}} P_{\text{cap}} - E_{\text{contract}} P_{\text{load}}}{E_{\text{contract}}} + (\bar{P} - S)
= \underbrace{(\bar{P} - S)}_{\text{1. Base price level}}
+ \underbrace{(P_{\text{cap}} - \bar{P})}_{\text{2. Profile / shape risk}}
+ \underbrace{\left(\frac{E_{\text{gen}} - E_{\text{contract}}}{E_{\text{contract}}}\right)\bar{P}}_{\text{3. Volume risk}}
+ \underbrace{\left(\frac{E_{\text{gen}} - E_{\text{contract}}}{E_{\text{contract}}}\right)(P_{\text{cap}} - \bar{P}) + (\bar{P} - P_{\text{load}})}_{\text{4. Cross-term (weather-price covariance) & load shape}}
```

For a flat baseload contract ($L_t = E_{\text{contract}} / T$, so $P_{\text{load}} \equiv \bar{P}$), the CfD leg
$E_{\text{contract}}(S - \bar{P})$ hedges Term 1 (`Base price level`), leaving the seller's net Baseload PPA revenue per
contracted MWh as $S + \text{Term}_2 + \text{Term}_3 + \text{Term}_4$. Taking expectations across multi-year weather
realisations with $\mathbb{E}[E_{\text{gen}}] = E_{\text{contract}}$ gives
$\mathbb{E}[\text{Term}_4] = \mathrm{Cov}(E_{\text{gen}}/E_{\text{contract}}, P_{\text{cap}} - \bar{P})$.

On an hourly basis, partitioning hours into surplus intervals ($g_t > L_t$) and deficit intervals ($g_t < L_t$) yields
the exact residual balancing identity:

```math
\sum_{t: g_t > L_t} (g_t - L_t) P_t - \sum_{t: g_t < L_t} (L_t - g_t) P_t
= E_{\text{surplus}} P_{\text{surplus}} - E_{\text{deficit}} P_{\text{deficit}}
\equiv E_{\text{gen}} P_{\text{cap}} - E_{\text{contract}} P_{\text{load}}
```

### Negative-price floor clauses and fair strike uplift

Under a negative-price clause active on hour set $\mathcal{H}_{\text{clause}}$ ($\mathcal{H}_{\text{zero}} = \{t : P_t < 0\}$
for a strict zero-price floor, or $\mathcal{H}_{6\text{h}} = \{t : t \text{ belongs to a run of } \ge 6 \text{ consecutive hours with } P_t < 0\}$
for the EU CfD 6-hour rule), the contractual CfD clawback is
$\text{Floor\_Clawback} = \sum_{t \in \mathcal{H}_{\text{clause}}} V_t (S - P_t)$, satisfying the exact accounting
identity across all structures and years:

```math
\text{Seller\_CfD\_Payout} + \text{Spot\_Revenue\_at\_Capture} = S \cdot E_{\text{settled}} + \text{Profile\_Gap} - \text{Floor\_Clawback}
```

When the generator curtails physical output during $t \in \mathcal{H}_{\text{clause}}$ to avoid paying negative spot
prices and the buyer suspends CfD settlement during those hours, a fraction
$\alpha = \sum_{t \in \mathcal{H}_{\text{clause}}} g_t / E_{\text{gen}}$ of annual generation earns zero revenue. To
preserve the target annual PPA revenue $S_0 E_{\text{gen}}$ over the remaining settled volume $(1 - \alpha)E_{\text{gen}}$,
the fair strike price must increase by:

```math
\Delta S_{\text{uplift}} = S_{\text{fair}} - S_0 = S_0\,\frac{\alpha}{1 - \alpha}
```

### Verification on analytical harmonic and Gaussian processes

`cppa_shaping.verification` checks both accounting identities and closed-form statistical recovery:

| Verification check | Analytical / Exact Truth | Model Estimate | Absolute Error |
|---|---|---|---|
| Hourly PPA settlement identity (all structures, clauses & mixes) | `0.000000 PLN/MWh` | `0.000000 PLN/MWh` | `< 1e-12 PLN/MWh` |
| Four-term decomposition bridge across all 500 bootstrap years | `0.000000 PLN/MWh` | `0.000000 PLN/MWh` | `< 1e-12 PLN/MWh` |
| Hourly surplus minus deficit identity (`Surplus_Rev - Deficit_Cost`) | `0.000000 PLN/MWh` | `0.000000 PLN/MWh` | `< 1e-12 PLN/MWh` |
| Harmonic diurnal process ($P_0 = 435$, $A_P = 391.5$, $\alpha_g = 0.8$, $\phi = \pi$): $P_{\text{cap}}$ | `278.400000 PLN/MWh` | `278.400000 PLN/MWh` | `< 1e-12 PLN/MWh` |
| Harmonic diurnal process: shape risk $\frac{1}{2}\alpha_g A_P \cos\phi$ | `-156.600000 PLN/MWh` | `-156.600000 PLN/MWh` | `< 1e-12 PLN/MWh` |
| Bivariate Gaussian process ($\sigma_E = 0.10$, $\sigma_P = 45.0$, $\rho = -0.60$): $\mathrm{Cov}(E_{\text{gen}}/E_c, P_{\text{cap}} - \bar{P})$ | `-2.700000 PLN/MWh` | `-2.700000 PLN/MWh` | `< 1e-12 PLN/MWh` |

## Run it

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"          # .venv/bin/pip on Linux/macOS
pytest -q                                      # 26 tests, no network, < 2 s
python -m cppa_shaping verify                  # accounting & analytical verification -> results/verification.json
python -m cppa_shaping run                     # full sweep, CSVs in results/, PNGs in docs/img/, docs/data.js
```

```
src/cppa_shaping/
  market.py        calibrated Polish 8,760-hour spot, PV, wind & buyer load profiles + 500-path block bootstrap
  contracts.py     Pay-as-Produced vs Baseload/Profiled CfD settlement, 4-term decomposition, negative-price clauses
  portfolio.py     hybrid PV + wind + 2h/4h BESS dispatch, 24/7 hourly CFE matching, P50/P90/CVaR sizing sweep
  verification.py  exact accounting checks (< 1e-9 PLN/MWh) and harmonic/Gaussian analytical recovery
  pipeline.py      end-to-end tables, Matplotlib figures, and dashboard payload builder
  cli.py           command-line interface (`python -m cppa_shaping run | verify`)
tests/
  test_cppa_shaping.py
results/
  portfolio_sweep.csv
  negative_price_clauses.csv
  mc_bootstrap_500yr.csv
  verification.json
docs/
  index.html       interactive Corporate PPA structuring dashboard (Plotly)
  data.js          precomputed sweep, clause tables, 500-path MC summary, and hourly windows
  img/             README Matplotlib charts
```

## Limitations

- **Calibrated synthetic market series.** The 8,760-hour reference year and 500-path block-bootstrap ensemble are
  generated deterministically from calibrated Polish market parameters (`435 PLN/MWh` mean spot price, `0.64` PV
  capture factor, `0.94` wind capture factor, `354` negative/zero price hours) so that tests and CLI runs complete
  offline in under two seconds. Site-specific PPA pricing requires multi-year nodal/zonal SCADA and TGE/PSE historical
  order-book data.
- **Co-located BESS dispatch.** The battery dispatch model uses a causal rule-based surplus-absorption and peak-deficit
  shaving policy (`88.5 %` round-trip efficiency at the PCC) to avoid perfect-foresight lookahead bias; it does not
  co-optimise ancillary service capacity bids (aFRR/mFRR) or PSE capacity-market obligations.
- **Guarantees of Origin (GoO) and balancing-group fees.** All settlement values represent wholesale electrical energy
  and CfD cashflows in `PLN/MWh`, excluding separate GoO certificate pricing, OzE/cogeneration surcharges, and TSO/DSO
  distribution tariffs.

## License

Code: MIT.
