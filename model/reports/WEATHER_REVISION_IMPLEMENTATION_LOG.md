# Pilot implementation corrections

The collection protocol, cohort, products, schedule, measurements and four existing strategy policies remain fixed. Each capture records its actual code hashes and commit. Earlier receipts and measurements are retained unchanged.

## September 9, 2026: portable gzip storage

The first manual capture used the code frozen in commit `2a73347`. Its original data passed independent reconstruction before this correction.

Before the first hosted run, the archive writer was advanced to `revision-archive-v2-portable-gzip`. Python 3.11/3.12 can write a different gzip OS header byte from Python 3.13, even for identical response bytes. The local collector uses Python 3.13 and the workflow uses Python 3.12. Comparing the compressed files byte-for-byte would therefore falsely reject some unchanged repeat responses. [Python's documented gzip version changes](https://docs.python.org/3/library/gzip.html#gzip.compress).

An existing body now remains untouched when its decompressed original bytes exactly equal the newly received original bytes. Different original contents still fail the immutable-file check. Receipt and body hashes continue to authenticate the original uncompressed representation. New receipts identify the writer version. A regression exercises distinct gzip headers for the same body and verifies that the first stored file is retained. This changes storage compatibility only; it does not reparse, replace or retimestamp the first capture.

## September 9, 2026: source history in hosted audits

The first daily deployment attempt passed model tests but failed four audit tests because the default shallow Git checkout omitted the earlier source commit referenced by the first capture. The daily and pilot workflows now fetch repository history so the independent audit can verify those recorded source blobs. Tests and data-integrity assertions were retained. This changes checkout availability only, without altering collection or strategy behavior.
