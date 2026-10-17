# Assumptions to confirm

Every value here is a placeholder for the simulator and the demo. Each one moves into
`config/` and is replaced when pilot data or a source is available.

## Hardware and loads

| Parameter | Placeholder | Status |
| --- | --- | --- |
| Pilot | 1 station, 1.8 kW, 6 households/day | Given (Green-Stove pilot) |
| Village station PV | 15 kWp each, 4 stations, 40 households each | 15 kW given; count is a placeholder |
| Inverter limit | 12 kW per station | To confirm |
| Battery | 30 kWh LFP per station, initial SoC 0.6 | To confirm |
| Battery floor (committee) / hardware LVD | 0.20 / 0.10 | To confirm |
| PV derate | 0.80 | To confirm |
| Stove | 1.0 kW electric pressure cooker, draw ±8% | To confirm |
| Session | 30 minutes, 0.5 kWh per meal | To confirm with pilot data |
| Electric meals | 2 per household per day (lunch, dinner) | To confirm |
| Clinic tier 1 | Vaccine fridge 0.12 kW, 24 h | To confirm |
| Clinic tier 2 | Maternity lights 0.25 kW, 18:00–06:00 | To confirm |
| Clinic tier 3 | Staff charging 0.30 kW, 08:00–17:00 | To confirm |
| Genset | 5 kW at the clinic station only | To confirm |
| Controller hardware for local model | Unknown; a 30B-A3B model needs a quantized build and tens of GB of memory | Open question |

## Committee bounds (demo village, ETB)

| Parameter | Placeholder |
| --- | --- |
| Price floor / ceiling | 4 / 20 per kWh |
| Lifeline | 2 per kWh, up to 1 kWh per day, priority households |
| Max daily price change | ±25% per tier |
| Default tariff | Sun 10:00–15:00 at 5; standard 8; peak 17:30–20:30 at 12 |
| Revenue target | 1,000 per village-day |
| Max household spend | 10 per day |
| Max meal delay | 60 minutes |
| Min household service | 0.9 over 7 days |
| Relaxation order | revenue → max delay → service → wood meals |
| Planting credit | 5 per tree (half at planting, half at survival check), cap 10 trees and 50 per month |
| Survival check | 30 days (7 in the 20-day eval) |

## Households and behavior

| Parameter | Placeholder |
| --- | --- |
| Priority share | 10% |
| Languages | Tigrinya 55%, Amharic 25%, Afaan Oromo 15%, English 5% |
| Preferred lunch / dinner | Normal around 12:30 (σ 40 min) / 18:45 (σ 35 min) |
| Window | Lunch −150/+60 min; dinner −240/+90 min |
| Delay cost | 0.8–3.0 per hour |
| Patience | 30–90 minutes |
| Compliance | 0.85 (0.75 in the downside scenario) |
| Wood per meal | 1.2 kg |
| Collection hours per wood meal | 0.6 |
| Forecast downside factor | 0.75 |

## Claims in the pitch that need a source before submission

| Claim | Note |
| --- | --- |
| ~140 million people without electricity in the Horn of Africa | Cite the source and year |
| ~90% of household energy from wood and charcoal; 97% in Southern Ethiopia | Cite the source |
| Traditional stoves convert 30–40% of fuel into usable heat | Check: three-stone fires are often reported well below this; the range may describe improved stoves |
| Green-Stove's 1.8 kW pilot cooks for six households a day | Pilot record |

## Open questions

- Ethiopian or international clock in SMS times.
- Native-speaker review of SMS templates and banned-promise lists for each language.
- Which SMS gateway would be used after the hackathon (out of scope now).
