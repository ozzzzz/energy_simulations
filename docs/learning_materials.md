# Learning materials: data center cooling & energy (real-world)

## Courses

- [DCEP Training](https://datacenters.lbl.gov/dcep) — LBL, Generalist/HVAC/IT/Electrical specialist tracks, real facility breakdowns
- [Uptime Institute CDCEP](https://uptimeinstitute.com/education/certified-data-center-energy-professional-cdcep) — energy audit, capacity reclamation, practical energy strategies
- [DCD Academy Cooling Professional](https://www.datacenterdynamics.com/en/dcdacademy/all-courses-certifications/cooling-professional/) — liquid cooling, thermal management, leads to DCS cert
- [Class Central: Data Center Cooling](https://www.classcentral.com/subject/data-center-cooling) — 50+ courses, liquid cooling for AI workloads
- [NC State MAE 589/619](https://engineeringonline.ncsu.edu/mae-589-619-electronic-and-data-center-cooling) — academic, single/two-phase cooling, immersion

## Articles / reference docs

- [ASHRAE Thermal Guidelines overview](https://www.sunbirddcim.com/glossary/ashrae-thermal-guidelines) — baseline standard, rack inlet temp/humidity ranges
- [ASHRAE 2021 reference card (PDF)](https://www.ashrae.org/file%20library/technical%20resources/bookstore/supplemental%20files/therm-gdlns-5th-r-e-refcard.pdf) — exact ranges by class A1–A4
- [LBL: Thermal Guidelines and Temperature Measurements (PDF)](https://datacenters.lbl.gov/sites/default/files/FINAL%20Thermal%20Guidelines%20and%20Temp%20Measurements%209-15-2020.pdf) — sensor placement, real measurement practice — relevant to `rack.py`/`telemetry.py`
- [G&D Chillers — what temp should a DC be](https://gdchillers.com/resources/data-centers/what-temperature-should-a-data-center-be-ashrae-guidelines-explained) — temp vs energy trade-off, plain explanation

## Notes for this project

Check `rack.py` / `telemetry.py` thresholds against ASHRAE class A1–A4 ranges above.
