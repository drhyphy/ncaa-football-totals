# Score-shape independent numerical audit

Audit status: **passed**. Reproduced all four annual shape fits and both saved references, 3,179 games per configuration. The independent two-moment solver uses SciPy trust-region least squares, not the original Newton implementation.

Checked 1,042,299 values; largest absolute difference 2e-09. Reproduced choice: `ridge_normal`.

Checks include source hashes, paired identities/cutoffs, stage Git ancestry, probability/loss/moment outputs, ratio counts, reliability, and pooled/year/source 95% and 99% week-bootstrap intervals. Full details and artifact hashes: [audit JSON](score_shape_numerical_audit.json).

This is numerical reproduction of reused development data, not evidence of an executable betting edge. Ridge centers come from their pinned saved OOF forecasts; this audit does not reimplement ridge fitting. Git ancestry and local receipts do not certify original public availability.
