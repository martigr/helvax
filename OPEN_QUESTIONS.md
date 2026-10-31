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

## Diphtheria, tetanus (`dip`, `tet`)

- **Interval after a dose at 16–24:** S. 14 1.4 a) says 10 years; Tab. 2 (row 11–24 years) says the next
  booster is at 25. helvax follows S. 14 (10 years) for doses given at 16–24, and "at 25" for doses given
  before 16.
- **Incomplete childhood series** (Tab. 3 and Tab. 4) are not computed. When the 4–7 or the 11–15 booster
  window has passed without the dose, helvax shows "Nachholschema mit Ärztin/Arzt festlegen".
- **Second dose before 4 months** (S. 8: then a third dose one month later and a fourth at 12 months) and
  infants vaccinated by the rules before 2019 (third dose at 6 months, S. 9) are not modelled.
- **Minimum interval of 2 years** before the 4–7 and the 11–15 boosters is taken from Tab. 2 Fn. 3 and applied
  to the routine schedule as well.
- **Shorter booster intervals** for immunodeficiency, travel or exposure (S. 6 Fn. 13) are not modelled.

## Pertussis (`pert`)

- From 8 years the catch-up series has fewer pertussis doses (Tab. 2: dTpa, dTpa, dT; from 11 only the
  first dose). helvax models these as shorter pertussis series.
- The dose at 25 needs a minimum interval of 2 years to the last tetanus dose (S. 14); helvax doesn't check
  intervals across antigens.
- A missed 11–15 booster ends the childhood pertussis series (no catch-up, `if_missed: skip`); the dose at 25
  follows. Please confirm.
- Extra doses for contact with infants under 6 months and in every pregnancy (Kapitel 3.1) are not computed.

## Polio (`polio`)

- "3+1" schedule (3 doses in the first year): a complete childhood series needs 5 doses (S. 6 Fn. 10). helvax
  only models the 2+1 schedule plus the 4–7 booster (4 doses).
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
- From 65: one dose, unless already vaccinated with PCV20/PCV21 at 65 or later. Previous PCV13, PCV15 or PPV23
  doses lead to different recommendations (S. 16). helvax doesn't know the product valency yet and treats
  any PCV dose from 65 on as the one dose.

## Meningococci ACWY (`menacwy`)

- 12–23 months: 1 or 2 doses depending on the vaccine (S. 12 j), Tab. 2 Fn. 19). helvax models 1 dose.

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
- Children aged 1–2: individual decision, not computed (recommended from 3 years).

## Not modelled at all (yet)

- Recommendations for risk groups and situations (Kapitel 3), except FSME; travel vaccinations.
- Premature infants (Tab. 6 and 7), healthcare workers (Tab. 9), tetanus prophylaxis after injuries
  (Tab. 10).
- Hepatitis A (risk groups and travel).

## Product catalogue

`helvax/catalogue/products-ch.yaml` maps trade names to antigens from general knowledge of the vaccines on the
Swiss market, not from the BAG schedule. Please check it against the Spezialitätenliste
(https://sl.bag.admin.ch/sl) or the product information. Product-dependent rules (MenACWY 1 or 2 doses, PCV
valency, HBV schema) don't use the product yet.
