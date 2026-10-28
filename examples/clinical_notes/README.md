# Synthetic neonatal chart

These three free-text notes describe one entirely fictional infant over admission, progress and discharge. They are written for a software demonstration, not clinical care or treatment advice.

- `01_neonatal_admission.txt`: presenting complaint, history, examination, differential diagnosis and plan.
- `02_neonatal_progress.txt`: SOAP-style progression, oxygen-support change and a pending test result.
- `03_neonatal_discharge.txt`: final diagnosis, hospital course and follow-up.

Only generic clinical-document conventions were used as a format reference. Patient identity, identifier, sex, gestational age, dates, measurements, diagnosis, interventions and course were independently invented. This is not a real patient's record with names replaced. No source PDF, email header, clinician identity, original patient details or copied narrative is included.

The case intentionally changes `suspected`/`rule out` and a pending culture at admission/progress to a resolved respiratory problem and a negative culture in the discharge note. A pseudonymization system must preserve these statements as written within each note. It must not infer the later outcome into the earlier note.

Exact clinical dates, elapsed time, GA, weight, sex, respiratory-support settings, measurements, feeding amounts and negation are preservation targets; the fictional name and chart number are pseudonymization targets. Each note is under the presentation live-input size limit as well as being available as a registered, precomputed example. The public simulation keeps its existing admission and GPU-budget controls.
