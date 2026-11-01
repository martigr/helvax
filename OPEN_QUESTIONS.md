# Open questions about the vaccination rules

`helvax/schedules/ch-2026.yaml` translates the *Schweizerischer Impfplan 2026* (BAG/EKIF,
`docs/sources/schweizerischer-impfplan-2026-de.pdf`) into rules the engine can compute. Writing a
document for doctors as data needed judgement calls. Each one is listed here, so someone with medical
knowledge can confirm or correct it. Rules involved are marked `review: true` in the YAML file
(`test_rules_marked_for_review_are_listed_in_open_questions` checks that every such antigen appears here).

Page numbers refer to the PDF. Nothing here is medical advice.

## App conventions (not from the schedule)

1. **When is a dose "overdue"?** The schedule gives recommended ages and age windows, not deadlines.
   helvax counts a dose as overdue after its window ends, or one month after its recommended date
   (`overdue_grace: 1m`), and boosters one year after their date (`booster_grace: 1y` for `dip`, `tet`, `tbe`).
   Catch-up doses of unvaccinated children and adults (`catch_up: true`) count as overdue at once.
2. **"Upcoming" horizon:** a booster or a not yet started vaccination shows as upcoming 90 days ahead
   (`engine.DEFAULT_HORIZON`).
3. **Doses given too early** (below the minimum age or interval) still count, with a warning. The only
   exception is the explicit rule for MMR doses before 9 months (S. 13 k), which don't count.
4. **Catch-up intervals:** Tab. 2 gives the third dose as "8 months after the first". The YAML writes it as
   an interval after the second (6 months after 0/2, 7 months after 0/1). Minimum intervals in the
   catch-up series (4 weeks between doses 1 and 2, 6 months before the third) follow the infant rules where
   Tab. 2 gives none.
5. **Dose numbers** count every dose of an antigen in order of date; a dose number typed in by hand
   (`dose_override`) is only shown, not used for the calculation.
6. **Incomplete records:** when childhood doses are missing from the booklet, helvax treats the person as
   unvaccinated and applies the catch-up series (e.g. an adult with only a dTpa at 25 and a dT at 45 gets a
   "third dose" instead of the next booster at 65). Should the app say "Ausweis unvollständig" instead?
7. **Unknown product** ("Anderer Impfstoff"): where a rule depends on the vaccine (MenACWY at 12–23 months,
   PCV from 65), the dose counts like the product that needs no further dose, with a warning to check it.

## Diphtheria, tetanus (`dip`, `tet`)

- **Interval after a dose at 16–24:** S. 14 1.4 a) says 10 years; Tab. 2 (row 11–24 years) says the next
  booster is at 25. helvax follows S. 14 (10 years) for doses given at 16–24, and "at 25" for doses given
  before 16.
- **Incomplete childhood series** (Tab. 3 and Tab. 4) are not computed. When the 4–7 or the 11–15 booster
  window has passed without the dose, helvax shows "Nachholschema mit Ärztin/Arzt festlegen".
- **Second dose before 4 months** (S. 8: then a third dose one month later and a fourth at 12 months): the
  given doses are read like the former 3+1 schedule (see below), but the extra third dose isn't planned.
- **Adults don't catch up missed childhood boosters** (`skip_from: 16y` on the 4–7 and 11–15 boosters of
  `dip`, `tet`, `pert`, `polio`): from 16 these slots are dropped, and a later dose counts as an adult booster.
  Based on Tab. 2 Fn. 5 ("Das Nachholen von in der Vergangenheit nicht erfolgten Auffrischimpfungen ist nicht
  erforderlich") and Tab. 4 (not modelled in detail). Children under 16 who missed a booster get the doctor hint.
- **Minimum interval of 2 years** before the 4–7 and the 11–15 boosters is taken from Tab. 2 Fn. 3 and applied
  to the routine schedule as well.
- **Shorter booster intervals** for immunodeficiency, travel or exposure (S. 6 Fn. 13) are not modelled.

## Former 3+1 infant schedule (`dip`, `tet`, `pert`, `polio`, `hib`, `hbv`)

Until 2018 infants got DTPa-IPV-Hib(-HBV) at 2, 4 and 6 months and a 4th dose in the second year of life
("3+1"). S. 9: they "brauchen weiterhin eine 4. Dosis ab dem Alter von 12 Monaten für einen vollständigen
Impfschutz (Mindestabstand zur 3. Dosis: 6 Monate)". S. 13 1.2 a): 4 doses DTPa-IPV (Hib-HBV) before school age
"wenn die 1. Dosis vor dem Alter von 6 Monaten und wenn mit einem «3+1»-Impfschema geimpft wurde (3 Dosen vor dem
Alter von 12 Monaten, 4. Dosis im 2. Lebensjahr)"; polio then 5 doses (S. 6 Fn. 10), pertussis 6 (S. 20 Tab. 3
Fn. 6). The series starting before 6 months (`start_age: [0d, 3m]` and `[3m, 6m]`) have an `optional` third dose
after the second. Judgement calls:

- **Which dose is the 3+1 third dose:** one given **before 11 months**, not "before 12 months" as S. 13 says. A
  dose at 11 months is the third dose of the 2+1 schedule (minimum age 11 months, S. 8), so it completes the
  series. Consequence: a 2+1 third dose given too early (before 11 months) now asks for a 4th dose instead of
  showing a warning.
- **Minimum interval to the optional dose:** 4 weeks, like the second dose. The schedule from before 2019 isn't
  in `docs/sources/`, so its own minimum intervals are not known.
- **4th dose:** the existing 12-month slot (minimum age 11 months per S. 8, though S. 9 says "ab dem Alter von 12
  Monaten"; minimum interval 6 months per S. 9). After it the 4–7 booster (minimum interval 2 years) and for
  `dip`, `tet`, `pert` the 11–15 booster follow as usual.
- **Hib and HBV:** S. 9 and S. 13 name them in parentheses ("Hib(-HBV)", "(Hib-HBV)"); the optional dose applies to
  both.
- The optional dose is never planned: for a child with two doses, helvax plans the 2+1 schedule. Neither the
  accelerated 3+1 schedule (2-3-4-12 months, S. 11) nor the extra dose after a second dose before 4 months (S. 8)
  is planned; given doses of both are read correctly.
- PCV isn't included: its 3+1 schedule (2-4-6-12 months) only applies after a PCV20 dose before 7 months (S. 11 g).

## Pertussis (`pert`)

- From 8 years the catch-up series has fewer pertussis doses (Tab. 2: dTpa, dTpa, dT; from 11 only the
  first dose). helvax models these as shorter pertussis series.
- The dose at 25 needs a minimum interval of 2 years to the last tetanus dose (S. 14); helvax doesn't check
  intervals across antigens.
- A missed 11–15 booster ends the childhood pertussis series (no catch-up, `if_missed: skip`); the dose at 25
  follows. Please confirm.
- Extra doses for contact with infants under 6 months and in every pregnancy (Kapitel 3.1) are not computed.

## Polio (`polio`)

- "3+1" schedule (3 doses in the first year): a complete childhood series needs 5 doses (S. 6 Fn. 10); see
  "Former 3+1 infant schedule" below.
- Catch-up 4–7 and 8–10 years are merged into one series (identical in Tab. 2).

## Hepatitis B (`hbv`)

- 11–15 years: helvax uses the 2-dose schema (0, 4–6 months, adult vaccine). The 3-dose schema with a
  children's vaccine (0, 1, 6 months) is also allowed (Tab. 2 Fn. 11) and depends on the product.
- Tab. 2 has no row for unvaccinated children aged 1–10; helvax waits for 11 years ("möglichst im Alter
  11–15 Jahre", S. 6 Fn. 11).
- From 16 only with exposure risk (Kapitel 3.1 c): not computed. Newborns of HBsAg-positive mothers: not
  modelled.

## Pneumococci (`pcv`)

- Infant third dose: the schedule says "before 13 months" for DTPa-IPV-Hib-HBV and PCV; the minimum age of
  11 months is only stated for the hexavalent vaccine, so helvax uses none for PCV.
- From 65: one dose of PCV20 or PCV21 (S. 16). A PCV13 or PCV15 dose given from 65 doesn't count; the
  higher-valent dose follows at least 1 year later. After PCV15, S. 16 names only PCV21; helvax accepts PCV20
  too (the rule only knows "PCV20 or PCV21"). Doses before 65 (risk persons) don't count for the dose from 65,
  following "im Alter von ≥ 65 Jahren erhalten". PPV23 can't be recorded (see the product catalogue).
- Infant third dose: no minimum interval to the second dose either (the catch-up series use 6 months), so the
  earliest date shown is the birth date.

## Meningococci ACWY (`menacwy`)

- 12–23 months: 1 or 2 doses depending on the vaccine (S. 12 j), Tab. 2 Fn. 19 on S. 19); S. 26 Fn. ** names them:
  Menveo 2 doses, MenQuadfi 1. helvax asks for a second dose after Menveo, 2 months later, at least 2 months
  (S. 12 j); S. 26 says "minimal 1 Monat" for risk groups). This applies when the first dose was given at 12–23
  months, also in the catch-up; from 24 months 1 dose. Nimenrix (not authorised in Switzerland) counts as 1
  dose.

## Varicella (`varicella`)

- Catch-up until the 40th birthday for people without chickenpox in their history (S. 17). Chickenpox in the
  history is entered as an immunity in the profile.
- Whether doses before 9 months count is not stated for varicella; helvax counts them (unlike MMR).

## HPV (`hpv`)

- 11–14 two doses with an interval of 6 months (S. 13); no minimum interval is given for the 2-dose schema,
  so helvax uses none.
- 11–14 with immunodeficiency need 3 doses: not modelled.
- 20–26 is a supplementary vaccination (opt-in); 15–19 is the catch-up of the basic vaccination.

## Herpes zoster (`zoster`)

- Only for immunocompetent people (S. 15); helvax can't tell, the profile owner opts in.
- The minimum interval of 2 months to an earlier live vaccine (Zostavax) is not checked.

## Influenza and Covid-19 (`influenza`, `covid19`)

- Season window: "zwischen Mitte Oktober und dem Beginn der Grippewelle" (influenza) and "idealerweise
  zwischen Mitte Oktober und Dezember" (Covid-19). helvax recommends 15 October to 31 December, and counts a
  dose given since 1 September as covering the season (app convention).
- After the window, the vaccination shows as overdue until the next season starts.
- Covid-19: "frühestens sechs Monate nach der letzten Impfung oder bestätigten Infektion" — infections are
  not recorded, only doses.

## RSV antibodies (`rsv`)

- Born October–March: in the first week of life; if not given by 31 March, in the following October (until
  the day before the first birthday). Born April–September: in October "or as soon as possible after";
  helvax shows it as overdue after 31 October.
- Exceptions where an antibody dose is recommended despite maternal vaccination (premature birth before
  37+0 weeks, maternal immunosuppression, S. 10) are only mentioned in the note, not computed.
- The maternal vaccination itself (for pregnant women) is not modelled.

## Tick-borne encephalitis (`tbe`)

- Risk group (Kapitel 3.1 a): the whole of Switzerland except Ticino. Computed only when the profile has the
  flag "FSME-Risikogebiet".
- Interval between the 2nd and 3rd dose depends on the vaccine (5–12 months after the second); helvax uses
  5 months. The accelerated schema (14 days between the first two doses) is accepted as the minimum.
  With `overdue_grace` the third dose is overdue 6 months after the second, although some vaccines allow up to
  12 months.
- Children aged 1–2: individual decision, not computed (recommended from 3 years).

## Not modelled at all (yet)

- Recommendations for risk groups and situations (Kapitel 3), except FSME; travel vaccinations.
- Premature infants (Tab. 6 and 7), healthcare workers (Tab. 9), tetanus prophylaxis after injuries
  (Tab. 10).
- Hepatitis A (risk groups and travel).

## Record-only vaccinations (app convention)

So a booklet can be recorded completely, the schedule file declares antigens with `record_only: true` and no
rule: MenC (`menc`), PPV23 (`ppv23`), hepatitis A (`hav`), the RSV vaccine for adults (`rsv_vaccine`), BCG
(`bcg`), smallpox/mpox (`smallpox`), yellow fever, typhoid, rabies, Japanese encephalitis, cholera, dengue and
`unlisted` ("Andere (nicht aufgeführt)"). Their doses are shown and numbered, but never evaluated: no status,
no reminders. They don't count for any rule either:

- A MenC dose (NeisVac-C, Menjugate) doesn't count as a MenACWY dose.
- A PPV23 dose doesn't count as a PCV dose (S. 16). It isn't used for the PCV recommendation from 65 either,
  although a previous PPV23 dose changes it there (S. 16): please check whether that should be modelled.
- The RSV vaccines for adults (Arexvy, Abrysvo) are not the infant antibody (`rsv`).
- Twinrix is now `hav` + `hbv`: the dose counts for hepatitis B as before; hepatitis A is only recorded.
  Entries saved before keep only `hbv` until they are edited and saved again.

## Product catalogue

`helvax/catalogue/products-ch.yaml` maps trade names to antigens. It is not from the BAG schedule. Checked on
2026-10-31 against the Swissmedic list of authorised human medicines (as of 2026-09-30) and the
Spezialitätenliste (SL, IT code 08.08); antigens follow the ingredients listed there. The SL only lists
reimbursed products (Gardasil 9, for instance, is missing there because the cantonal programmes pay for it).

- **Not on the Swissmedic list:** Hexyon, Td-pur, RotaTeq, Nimenrix, Trumenba. They stay in the list for old or
  foreign records, in the group «Nicht in der Schweiz zugelassen». S. 16 says the only dT vaccine authorised for
  adolescents and adults "ist nicht erhältlich"; whether Td-pur was authorised before isn't checked.
- **Added:** Adacel-Polio (dTpa-IPV), Hiberix (Hib), Twinrix (counts as hepatitis B only; hepatitis A is not
  modelled), mNEXSPIKE (Covid-19), Enflonsia (clesrovimab). Enflonsia counts as the RSV antibody dose: the
  schedule recommends "eine einmalige Dosis eines langwirkenden mAk" (S. 1), without naming a product for
  infants in their first year; for 12–24 months it names "aktuell 200 mg Nirsevimab" (S. 28). Please confirm.
- **Left out, because they would count as the wrong antigen:** Zostavax (S. 15: Shingrix is needed anyway)
  and palivizumab (Synagis, monthly, not a long-acting mAb). Such doses can be recorded as "Anderer
  Impfstoff" with a record-only antigen (e.g. `unlisted`).
- **Record-only group «Ohne Auswertung»:** NeisVac-C, Menjugate, Pneumovax 23, Havrix, Arexvy, Abrysvo, BCG,
  Stamaril, Vivotif, Typhim Vi, Rabipur, Verorab, Ixiaro, Dukoral, Qdenga, Jynneos/Imvanex. Their
  authorisation and ingredients were **not** checked against the Swissmedic list; since they are never
  evaluated, a wrong entry only affects the label in the record.
- **Product-dependent rules:** MenACWY at 12–23 months and PCV from 65 use the `tags` of the products. Not
  yet: HBV 11–15 adult or children's dose (Engerix-B 10/20 and HBVAXPRO 10/40 are one entry each; Tab. 2
  Fn. 11).

## eVACDOC import (`flask convert-evacdoc`)

Not from the schedule: how an eVACDOC file (HL7 CDA-CH-VACD, e.g. the export of the former meineimpfungen.ch)
becomes import rows (`helvax/evacdoc.py`).

- **Antigens from the SNOMED CT reason codes** of each vaccination ("Diphtheria vaccination (procedure)" etc.,
  `SNOMED_ANTIGENS`), not from the product name or the ATC code. Only codes that name one antigen of the
  schedule are mapped. "Meningococcus vaccination" (41088001) doesn't say the serogroup (MenC, MenACWY or
  MenB), so such entries are left out and reported; so is anything else without an antigen. BCG (42284007)
  maps to the record-only antigen `bcg`.
- **Product:** a catalogue product only if its name (or a name the document's name starts with, e.g.
  "FSME-Immun CC" → FSME-Immun) matches *and* it covers exactly the antigens of the document; otherwise
  "Anderer Impfstoff" with the document's name and antigens. Old products (Poloral, DiTePer Anatoxal,
  Pluserix, ...) therefore come in as "other".
- **Not taken over:** planned entries (the exporting system's own recommendations, LOINC 18776-5), negated or
  not completed entries, past illnesses and the problem list (reported; immunities and risk factors are set
  in the app). "Dokumentiert durch" (author) isn't the vaccinating person and isn't imported; `administered_by`
  comes from `performer` only.
