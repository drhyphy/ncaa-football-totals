# NOAA request and forecast-source audit

Status: frozen request-plan checks passed; downloader and extraction review in progress. No forecast values, weather classifications, game outcomes, or returns were inspected for this audit.

## Request-plan reconstruction

An independent calculation, importing no project planning or evaluation module, verified plan SHA-256 `47747f5a0216b5b0a43e9df5777575bfafb165ad6616332869eda35c2b508258` and all 13 frozen input hashes. It reconstructed dates, six-hour initialization selection, request membership, interpolation weights, and byte-range field requirements directly from the JSON plan.

| Check | Result |
| --- | ---: |
| Earlier repaired market universe | 2,607 distinct games |
| Included games | 1,747 |
| Disjoint exclusions | 860 |
| Shared forecast objects | 1,275 |
| Unique required field ranges | 3,572 |
| Game-to-field associations | 17,470: exactly ten per game |
| Minimum/maximum field forecast lead | 48 / 56 hours |
| Decision time minus initialization | 34.5–47.5 hours |
| Schedule kickoff exactly equal to retrospective ESPN event kickoff | 1,747 of 1,747 |

Every game uses one initialization for four consecutive hours. Kickoff-floor-hour temperature and humidity accompany all four U/V wind pairs. Every object's cutoff is the earliest decision among its linked games, and its fields are exactly the union of those games' required fields. Bilinear weights are nonnegative, sum to one, and reproduce the catalog coordinates. Request IDs and inventory URLs match the frozen product.

The 860 exclusions are 697 actual venues outside the fixed catalog, 101 neutral/unknown-neutral games, 61 indoor/unknown-roof games, and one missing/ambiguous historical-event identity. These are coverage restrictions, not a representative random sample of all college football. The fixed all-Under benchmark must therefore use the same weather-available cohort. No excluded venue should be added after seeing results.

## Source timing: material qualification

The NOAA-managed public AWS registry identifies this bucket as GFS forecast data, with four cycles per day. Its operational product should be described separately from a reanalysis or retrospective hindcast. Product identity still needs the exact original GRIB initialization, lead, field, level, and grid checks. [NOAA GFS public archive registry](https://registry.opendata.aws/noaa-gfs-bdp-pds/).

AWS documents Last-Modified as system-controlled metadata. However, when an object was created through multipart upload, its creation timestamp refers to **upload initiation**. It does not by itself identify completion or prove public availability at that instant. ETags identify object versions; a multipart ETag must not be treated as a plain-file MD5 checksum. [Amazon S3 object metadata](https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingMetadata.html).

The implementation decision, made before classification, preserves the frozen Last-Modified-before-decision gate and initialization-plus-six-hours buffer. It records multipart ETag status and labels timing as a historical S3 availability proxy. It does not invent a publication-completion time or discard multipart objects after observing their weather. This provides evidence against later replacement but is weaker than an actual archived client receipt before the decision. It remains a limitation even when the nominal initialization is more than a day before that decision.

## Downloader and extractor requirements

The reviewed downloader freezes complete-message offsets from exact inventory selectors, requires HTTP 206 and exact Content-Range/Length, checks the object's ETag and Last-Modified again, and archives byte hashes. Unexpected whole-object responses are rejected before reading their body. Missing or invalid fields cannot trigger a replacement cycle or product. The full inventory must be complete before field retrieval, and the complete field archive must precede classification.

The extractor's reviewed pure functions validate the exact quarter-degree grid, all four scan-order flags, forecast units and valid times, and earth-relative wind components. Those checks matter because reshaping an unexpected scan order can silently assign values to the wrong stadium. U/V components are interpolated separately before their scalar speed; four hourly speeds are then averaged. Temperature and humidity use only the kickoff-floor hour. The published strict three-condition intersection remains unchanged.

Archive integration, independent complete-field extraction, and outcome joining remain to be checked when their implementation is ready. This report does not certify future or unfinished stages.

## Interpretation boundaries

Historical ESPN event-specific venue IDs are stronger identity evidence than current home-stadium inference, but the event responses were retrieved retrospectively; historic roof state and possible schedule corrections are not contemporaneously captured facts. All compared kickoffs happen to agree exactly in this frozen cohort.

The NOAA grid interpolation and fixed single initialization differ from the existing Open-Meteo previous-day forecast implementation. Both also differ from the original paper's realized weather measurements. A favorable result would be a forecast-source replication of the fixed hypothesis, not proof that the original paper's realized-weather probability transfers to these forecasts. The earlier outcomes were reused elsewhere in this project. Assumed −110 settlement cannot establish executable morning EV without actual offered prices and quote receipts.
