<!-- table:market -->
| Window | Hours | Mean RCE (PLN/MWh) | PV capture (PLN/MWh, factor) | Wind capture (PLN/MWh, factor) | Hours < 0 | Hours in runs >= 6 h (runs) | Load-weighted price two-shift / cold storage |
|---|---|---|---|---|---|---|---|
| 2025 | 8760 | 441.9 | 282.7 (0.64) | 414.3 (0.94) | 352 | 191 (26) | 466.1 / 433.9 |
| Jan-Sep 2025 | 6551 | 426.8 | 266.5 (0.62) | 403.0 (0.94) | 344 | 191 (26) | 442.5 / 416.1 |
| Jan-Sep 2026 | 6551 | 504.9 | 298.6 (0.59) | 481.4 (0.95) | 322 | 167 (22) | 518.2 / 499.2 |

<!-- table:portfolio -->
| Portfolio (baseload, 100 GWh/yr, S = 380) | Shaping cost 2025 (PLN/MWh) | of which profile gap / curtailment gain / BESS gain | 24/7 CFE (%) | Deficit bought on spot (GWh/yr) | Bootstrap P50 / P90 (PLN/MWh) | Bootstrap std (PLN/MWh) |
|---|---|---|---|---|---|---|
| 100 % PV, 0 h BESS | 153.5 | 159.1 / 5.6 / 0.0 | 41.2 | 58.8 | 225.8 / 214.8 | 8.6 |
| 25 % wind, 0 h BESS | 122.1 | 126.2 / 4.1 / 0.0 | 57.4 | 42.6 | 257.0 / 247.0 | 7.9 |
| 50 % wind + 50 % PV, 0 h BESS | 90.7 | 93.3 / 2.6 / 0.0 | 70.6 | 29.4 | 288.3 / 276.7 | 9.8 |
| 75 % wind, 0 h BESS | 59.3 | 60.5 / 1.2 / 0.0 | 76.5 | 23.4 | 319.1 / 303.6 | 13.3 |
| 100 % wind, 0 h BESS | 27.0 | 27.6 / 0.5 / 0.0 | 70.7 | 29.3 | 351.6 / 330.8 | 17.4 |
| 100 % PV, 2 h BESS | 131.5 | 159.1 / 5.6 / 22.1 | 50.3 | 49.7 | 247.7 / 237.1 | 8.7 |
| 50 % wind + 50 % PV, 2 h BESS | 70.9 | 93.3 / 2.4 / 20.0 | 78.2 | 21.8 | 308.3 / 296.2 | 9.8 |
| 100 % wind, 2 h BESS | 28.1 | 27.6 / 0.5 / -1.1 | 73.7 | 26.3 | 350.6 / 329.3 | 17.6 |
| 100 % PV, 4 h BESS | 112.5 | 159.1 / 5.0 / 41.6 | 58.4 | 41.6 | 266.5 / 256.1 | 8.8 |
| 50 % wind + 50 % PV, 4 h BESS | 58.5 | 93.3 / 1.7 / 33.2 | 83.2 | 16.8 | 320.9 / 307.9 | 10.0 |
| 100 % wind, 4 h BESS | 27.7 | 27.6 / 0.5 / -0.6 | 75.4 | 24.6 | 351.3 / 330.0 | 17.6 |

<!-- table:best -->
| Buyer profile | BESS | Lowest shaping cost: wind share (PLN/MWh) | Highest 24/7 CFE: wind share (%) | Highest P90: wind share (PLN/MWh) | Lowest revenue std: wind share (PLN/MWh) |
|---|---|---|---|---|---|
| Flat Baseload (24/7) | 0 h | 100 % (27.0) | 80 % (76.6) | 100 % (330.8) | 20 % (7.8) |
| Flat Baseload (24/7) | 2 h | 100 % (28.1) | 70 % (81.3) | 100 % (329.3) | 20 % (8.0) |
| Flat Baseload (24/7) | 4 h | 100 % (27.7) | 60 % (84.2) | 100 % (330.0) | 20 % (8.2) |
| Two-Shift Industrial | 0 h | 100 % (51.2) | 60 % (70.9) | 100 % (305.9) | 15 % (8.9) |
| Two-Shift Industrial | 2 h | 100 % (50.9) | 55 % (79.6) | 100 % (307.3) | 15 % (9.2) |
| Two-Shift Industrial | 4 h | 100 % (52.5) | 50 % (83.0) | 100 % (305.7) | 15 % (9.6) |
| Cold-Storage / Food Processing | 0 h | 100 % (19.2) | 70 % (77.3) | 100 % (339.4) | 20 % (7.0) |
| Cold-Storage / Food Processing | 2 h | 100 % (22.1) | 60 % (82.4) | 100 % (336.9) | 20 % (7.1) |
| Cold-Storage / Food Processing | 4 h | 100 % (22.3) | 50 % (85.8) | 100 % (337.4) | 20 % (7.4) |

<!-- table:settlement -->
| Contract (2025, S = 380 PLN/MWh) | Profile gap carried by seller | Curtailment gain | BESS gain | Negative-price clawback | Seller revenue 2025 | Bootstrap P50 / P90 | Buyer cost per MWh of load |
|---|---|---|---|---|---|---|---|
| Pay-as-produced, 100 % PV, zero-price floor | 0.0 | 0.0 | 0.0 | 49.5 | 330.5 | 329.7 / 319.1 | 481.6 |
| Pay-as-produced, 100 % wind, zero-price floor | 0.0 | 0.0 | 0.0 | 14.7 | 365.3 | 363.9 / 345.7 | 390.6 |
| Baseload, 100 % PV, 0 h BESS | 159.1 | 5.6 | 0.0 | 0.0 | 226.5 | 225.8 / 214.8 | 380.0 |
| Baseload, 50 % wind + 50 % PV, 0 h BESS | 93.3 | 2.6 | 0.0 | 0.0 | 289.3 | 288.3 / 276.7 | 380.0 |
| Baseload, 50 % wind + 50 % PV, 2 h BESS | 93.3 | 2.4 | 20.0 | 0.0 | 309.1 | 308.3 / 296.2 | 380.0 |
| Baseload, 100 % wind, 0 h BESS | 27.6 | 0.5 | 0.0 | 0.0 | 353.0 | 351.6 / 330.8 | 380.0 |

<!-- table:bootstrap -->
| Mix (flat baseload, no BESS or curtailment) | Mean shape term (PLN/MWh) | Mean volume term | Mean cross term | Std of annual output (%) | corr(output, shape term) | corr(output, mean price) | Std of seller revenue (PLN/MWh) |
|---|---|---|---|---|---|---|---|
| 100 % PV | -159.4 | -0.45 | 0.10 | 2.25 | -0.25 | -0.28 | 9.0 |
| 50 % wind + 50 % PV | -93.5 | -0.54 | 0.10 | 2.25 | +0.15 | -0.71 | 9.8 |
| 100 % wind | -27.5 | -0.64 | -0.02 | 4.32 | -0.24 | -0.60 | 17.4 |

<!-- table:clauses -->
| Window | Mix (pay-as-produced, S = 380) | Clause hours: 6-hour rule / zero floor | Unsettled output: 6-hour rule / zero floor (%) | Strike uplift: 6-hour rule / zero floor (PLN/MWh) | Spot cost avoided by curtailing, zero floor (PLN/MWh) |
|---|---|---|---|---|---|
| 2025 | 100 % PV | 191 / 352 | 6.30 / 13.02 | +25.55 / +56.86 | 8.05 |
| 2025 | 50 % wind + 50 % PV | 191 / 352 | 4.28 / 8.45 | +17.01 / +35.05 | 5.14 |
| 2025 | 100 % wind | 191 / 352 | 2.27 / 3.87 | +8.82 / +15.32 | 2.23 |
| Jan-Sep 2025 | 100 % PV | 191 / 344 | 7.08 / 14.53 | +28.97 / +64.62 | 9.05 |
| Jan-Sep 2025 | 50 % wind + 50 % PV | 191 / 344 | 5.12 / 9.84 | +20.51 / +41.48 | 6.07 |
| Jan-Sep 2025 | 100 % wind | 191 / 344 | 3.16 / 5.15 | +12.40 / +20.64 | 3.10 |
| Jan-Sep 2026 | 100 % PV | 167 / 322 | 6.65 / 13.90 | +27.08 / +61.36 | 12.91 |
| Jan-Sep 2026 | 50 % wind + 50 % PV | 167 / 322 | 4.42 / 9.21 | +17.59 / +38.53 | 7.64 |
| Jan-Sep 2026 | 100 % wind | 167 / 322 | 2.20 / 4.51 | +8.53 / +17.95 | 2.36 |

<!-- table:comparison -->
| Mix (baseload, volume-matched in each window) | Shaping cost 0 h: Jan-Sep 2025 / Jan-Sep 2026 | Shaping cost 2 h BESS: Jan-Sep 2025 / Jan-Sep 2026 | Zero-floor uplift: Jan-Sep 2025 / Jan-Sep 2026 |
|---|---|---|---|
| 100 % PV | 154.4 / 197.7 | 126.8 / 161.7 | +64.62 / +61.36 |
| 50 % wind + 50 % PV | 89.3 / 111.6 | 62.9 / 79.4 | +41.48 / +38.53 |
| 100 % wind | 23.0 / 23.3 | 24.1 / 28.1 | +20.64 / +17.95 |
