# Original forecast archives for an earlier weather-rule test

**Feasible without a key or purchase.** Prefer NOAA's archived GFS 0.25° operational forecasts for a fixed 2021–2023 experiment: they supply hourly fields at stadium coordinates and preserve forecast initialization. IEM's GFS MOS archive is a cheaper route extending through 2016–2023, but its airport calibration and three-hour spacing make it a different measurement protocol. Neither route uses reanalysis. This feasibility check inspected a few forecast samples only; it did not join outcomes, calculate betting results, select weather-positive games, or change thresholds.

## Verified no-key requests

The following GET requests succeeded on September 9, 2026 UTC. Successful samples establish access, not complete seasonal coverage.

| Original product/sample | HTTP | Download | Verification |
|---|---:|---:|---|
| IEM GFS MOS, KAMW, 2016-09-10 00 UTC | 200 | 22,804 bytes | 21 valid-time rows; temperature, dewpoint, wind present |
| IEM GFS MOS, KAMW, 2021-09-11 00 UTC | 200 | 22,810 bytes | Same schema; explicit runtime and valid time |
| IEM GFS MOS, KAMW, 2023-09-09 00 UTC | 200 | 22,797 bytes | Same schema; explicit runtime and valid time |
| NOAA GFS, 2021-09-11 00 UTC, f048/f049 inventories | 200 | 41,272 / 41,276 bytes | Adjacent hourly forecast steps and required fields |
| NOAA GFS, 2022-09-10 / 2023-09-09 00 UTC, f048 inventories | 200 | 41,277 / 41,250 bytes | Required near-surface fields present |
| NOAA GFS, 2021 f048 temperature GRIB message only | 206 | 503,614 bytes | Decoded original initialization, lead, level and units |

**IEM exact-run JSON:**

```text
https://mesonet.agron.iastate.edu/api/1/mos.json?station=KAMW&runtime=2021-09-11%2000:00Z&model=GFS
```

Replace the date with `2016-09-10` or `2023-09-09` for the other verified samples. The response is a `schema`/`data` object; rows contain `station`, `model`, `runtime_utc`, `ftime_utc`, `tmp`, `dpt`, and `wsp`. Despite lacking a trailing `Z` in these sample strings, the `_utc` fields denote UTC. Always specify runtime; omitting it requests a recent run. Multiple `station=` parameters are documented for this API. A documented date-range alternative is below; this specific range endpoint was not sampled. [IEM archive/API overview](https://mesonet.agron.iastate.edu/mos/), [range endpoint documentation](https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py?help=).

```text
https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py?station=KAMW&model=GFS&sts=2021-09-11T00:00Z&ets=2021-09-11T00:00Z&format=csv
```

**NOAA exact forecast inventory and byte-range retrieval:**

```bash
curl -sS -L 'https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20210911/00/atmos/gfs.t00z.pgrb2.0p25.f048.idx' -o forecast.idx
curl -sS -L --range 416760029-417263642 'https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20210911/00/atmos/gfs.t00z.pgrb2.0p25.f048' -o temperature.grib2
```

The sample offsets apply **only to that object**. Parse every inventory to select complete GRIB messages; calculate each end offset as the next message's offset minus one. Never reuse offsets across dates or download the full object unnecessarily. The sampled full file was 541,020,405 bytes. ecCodes decoded the extracted field as 2-m temperature in kelvin, initialization `20210911 0000`, lead `48`, valid `20210913 0000`, a 1440×721 regular latitude/longitude grid. The server returned `Last-Modified: 2021-09-11 03:50:31 GMT`; retain that independently served metadata alongside the GRIB header, URL, receipt and content hash. It supports original operational chronology, but is not a universal guarantee against later archive replacement. [Verified sample inventory](https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20210911/00/atmos/gfs.t00z.pgrb2.0p25.f048.idx).

## Fields, archive period, and availability

| Needed input | GFS GRIB selection | MOS equivalent |
|---|---|---|
| Temperature | `TMP:2 m above ground`, K→°F | `tmp`, integer °F |
| Relative humidity | `RH:2 m above ground`, % | Derive from `tmp` and `dpt`; no direct RH field in sampled rows |
| Sustained wind | `UGRD` and `VGRD` at `10 m above ground`; vector magnitude in m/s→mph | `wsp`, integer knots→mph; 10-m, two-minute wind forecast |
| Timing | GRIB initialization plus forecast step | Model `runtime` plus `ftime` |

MOS temperature/dewpoint are 2-m forecasts. Its sampled 00 UTC cycles have three-hour values from +6 through +60 and six-hour values thereafter through +72. Missing temperature/dewpoint codes can be 999; missing wind is 99, whereas zero means calm. Preserve missingness before unit conversion. The IEM archive labels GFS coverage from December 16, 2003 onward; this check verified selected 2016/2021/2023 runs, not every station. [NWS original MOS format specification](https://www.weather.gov/media/mdl/mdltpb05-03.pdf), [IEM field descriptions](https://mesonet.agron.iastate.edu/mos/fe.phtml).

NCEI lists the modern GFS cloud period from February 26, 2021 and cycles at 00/06/12/18 UTC. Its general page also mentions a trailing window; the successful older S3 requests above demonstrate that those specific 2021–2023 objects remain accessible. NOAA's registry explicitly permits anonymous access. No 2016–2020 gridded GFS endpoint was verified here. [NCEI product information](https://www.ncei.noaa.gov/products/weather-climate-models/global-forecast), [NOAA open-data registry](https://registry.opendata.aws/noaa-gfs-bdp-pds/).

Initialization precedes publication. Keep the existing six-hour publication buffer instead of treating a run as available immediately. Freeze one rule for selecting eligible cycles before extraction; for example, for each required valid hour choose the latest 6-hourly initialization at least 48 hours earlier, additionally requiring initialization plus six hours no later than the 06:30 Eastern decision time. This gives 48–53-hour leads and needs explicit comparison with the existing archive-writer convention. Reject missing cycles; do not substitute analyses, ERA5, GDAS, retrospective reforecasts, or a later run.

An independent audit adds a specific S3 caveat: Last-Modified for multipart objects reflects upload initiation, not necessarily completed publication. Keep the original headers/ETag and label this an availability proxy; do not infer an independently certified public receipt solely from Last-Modified. [AWS metadata semantics](https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingMetadata.html).

## Mapping, measurement, and cost

Freeze historical venue IDs/coordinates and roof exclusions before looking at forecast values. Stadium relocations, neutral venues, renamed buildings, retractable roofs and airport relocations need dated evidence. GFS needs a fixed grid interpolation method; its roughly 0.25° cells smooth terrain, coastlines and stadium wind exposure. MOS needs a fixed nearest-station/distance/elevation policy using station metadata, not the station producing the best betting result. IEM publishes [station coordinates and operating dates](https://mesonet.agron.iastate.edu/sites/networks.php?network=IA_ASOS); current metadata still needs historical verification.

The fixed conditions remain wind >7.78 mph, temperature <64.81°F, RH >56.8%, with kickoff-hour temperature/RH and four hourly wind values. GFS can supply that resolution directly. MOS would require a preregistered interpolation and RH formula, with extra uncertainty from rounded temperature/dewpoint and knots. It must be labeled a forecast-source replication rather than an identical extraction. No outcome-driven choice among stations, interpolation methods, forecast cycles or products is justified.

Estimated service charge is **$0** for these anonymous public downloads; local bandwidth, storage and compute remain necessary. IEM costs about 23 KB per station/run: 2,500 requests are approximately 58 MB before compression, potentially fewer with shared runs. GFS's sampled TMP/RH/U/V messages total 3.19 MB per valid hour. Kickoff TMP/RH plus U/V at four hours costs about 8.9 MB per game without sharing—roughly 22 GB for 2,500 games. Cache by initialization/step/field and extract all planned venues from each field to reduce transfer substantially. Inventories allow an exact byte budget after the outcome-free request plan is locked. This check downloaded under 1 MB of sample data, excluding documentation. NOAA and IEM permit public reuse with appropriate attribution; cache responses and use modest concurrency. [NOAA usage terms](https://registry.opendata.aws/noaa-gfs-bdp-pds/), [IEM usage terms](https://mesonet.agron.iastate.edu/disclaimer.php).

The next step is one frozen 2021–2023 GFS request plan, with identities, source URLs, fields, timing, mapping, missingness rules and budget fixed before any weather/outcome evaluation. Earlier totals remain subject to their existing market-source and unknown price/timestamp limitations. Additional seasons would strengthen evidence only as a transparently reconstructed replication, not a retroactive prospective betting record.
