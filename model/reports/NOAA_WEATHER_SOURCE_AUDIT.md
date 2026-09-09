# NOAA request and forecast-source audit

Status: frozen request-plan, complete inventory, and implementation review passed. Complete downloaded-field and result audits remain pending. No forecast values, weather classifications, game outcomes, or returns were inspected for this audit.

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

## Complete inventory verification

An independent header-and-index audit verified inventory SHA-256 `f486e78a6a2a4699f3b7c54ae4f9186beab87ed04dcbd8aa234959767a734649` and its parent request-plan linkage. All 1,275 requested objects are available, with zero source exclusions. Every recorded object has a 32-hex-character ETag and none has the multipart suffix pattern. Thus the specific multipart-upload caveat is not indicated by the actual archived headers; the study still labels these timestamps as a historical availability proxy rather than client receipts.

Last-Modified occurs **3.704–4.050 hours after initialization** and **30.571–43.796 hours before the earliest affected decision**. Every inventory's original bytes match its hash. Every selected field has one exact selector, the correct initialization and lead, and a byte range extending to the next complete-message offset (or object end for the final message).

The verified inventory contains 511 temperature, 511 humidity, 1,275 U-wind, and 1,275 V-wind fields: 3,572 ranges totaling **3,151,825,716 bytes**. Individual fields span 422,299–1,014,378 bytes. No field body was needed for these checks, and this inventory audit does not certify their yet-unfinished download or decoding.

## Downloader and extractor requirements

The reviewed downloader freezes complete-message offsets from exact inventory selectors, requires HTTP 206 and exact Content-Range/Length, checks the object's ETag and Last-Modified again, and archives byte hashes. Unexpected whole-object responses are rejected before reading their body. Missing or invalid fields cannot trigger a replacement cycle or product. The full inventory must be complete before field retrieval, and the complete field archive must precede classification.

The extractor's reviewed pure functions validate the exact quarter-degree grid, all four scan-order flags, forecast units and valid times, and earth-relative wind components. Those checks matter because reshaping an unexpected scan order can silently assign values to the wrong stadium. U/V components are interpolated separately before their scalar speed; four hourly speeds are then averaged. Temperature and humidity use only the kickoff-floor hour. The published strict three-condition intersection remains unchanged.

The archive-integration code review checks the complete inventory and fetch manifests, exact object/field coverage, game/hour/field linkage conservation, immutable receipt hashes, and original HTTP/GRIB identity. Decode errors and required missing bitmap points become explicit unavailable weather; archive identity errors stop extraction. The separate classification artifact pins code, protocol, metadata, and source bytes before any outcome join.

The outcome-join code preserves repaired-source precedence, canonicalizes game IDs, and requires matching season/team/source identity plus final score agreement with the independent schedule. Its common-week bootstrap uses identical draws for the selected and all-Under ratios, includes zero-selection covered weeks, and reports draws with no selected stakes. Push settlement and active-week ratio-score t calculations match the frozen protocol. A schema-only check confirmed that the actual source Parquet files contain the required columns; no score values were read.

No material implementation defect was found in this review. Executing independent complete-field extraction and independently recomputing the eventual result remain pending. This report does not certify future or unfinished stages.

## Interpretation boundaries

Historical ESPN event-specific venue IDs are stronger identity evidence than current home-stadium inference, but the event responses were retrieved retrospectively; historic roof state and possible schedule corrections are not contemporaneously captured facts. All compared kickoffs happen to agree exactly in this frozen cohort.

The NOAA grid interpolation and fixed single initialization differ from the existing Open-Meteo previous-day forecast implementation. Both also differ from the original paper's realized weather measurements. A favorable result would be a forecast-source replication of the fixed hypothesis, not proof that the original paper's realized-weather probability transfers to these forecasts. The earlier outcomes were reused elsewhere in this project. Assumed −110 settlement cannot establish executable morning EV without actual offered prices and quote receipts.
