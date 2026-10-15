# Sentinel-1 SAR in geolens: what is possible

*Written 15 Oct 2026. Nothing here is built yet. One decision is needed from
Sam (at the end).*

## The question

Can Sentinel-1 radar be streamed and used the way NAIP NDVI is, and would it
add anything?

## Short answer

- **Not anonymously, the way NAIP is.** The Planetary Computer's
  terrain-corrected collection (`sentinel-1-rtc`) is marked
  `msft:requires_account: true`: its download tokens need a Planetary
  Computer account. The `sentinel-1-grd` collection is neither
  terrain-corrected nor map-projected. In valleys with hundreds of
  metres of relief, that geometry error is larger than a riparian strip, and
  geolens should not take on its own SAR processor.
- **The usable source is NASA's OPERA RTC-S1.** It is free, already
  terrain-corrected (gamma-naught), on a 30 m UTM grid as cloud-optimised
  GeoTIFFs, one per Sentinel-1 burst, from April 2016, near-global. Bulk
  download from ASF needs a free NASA Earthdata login.
- **It can be looked at today with no code and no login.** NASA publishes
  OPERA RTC-S1 VV and VH as time-enabled image services. Add them to
  ArcGIS Pro:
  - `https://gis.earthdata.nasa.gov/image/rest/services/OPERA_L2_RTC_S1_V1/OPERA_L2_RTC_S1_V1_VH/ImageServer`
  - the same address with `VV` in place of `VH`.

  The service metadata gives a time extent of Oct 2023 to Dec 2025.

## What SAR could add, and what it can't

**Flood extent: no** (already in `inundation.py`). In arid washes, flow is
rarely present at an overpass, and dry sand can look as dark to radar as
water.

**Separating woody riparian vegetation from irrigated fields: maybe.** That is
the one gap SAR could fill. NAIP NDVI cannot tell a cottonwood gallery from
an alfalfa field: both are green in June. Radar sees structure and change:

| Expected | VH backscatter | Through the year |
|---|---|---|
| Woody riparian | high (volume scattering in branches) | steady |
| Irrigated hay | moderate | jumps at every cutting and irrigation |
| Bare channel sand | low | steady, low |

If that holds, a per-pixel year of VH would flag NAIP "riparian" polygons
that behave like fields. That matters because a hay field read as riparian
widens the riparian band.

**Limits:**
- The pixels are 30 m, and riparian strips here are often 10–60 m wide, so
  many pixels are mixed. SAR would flag polygons; it would not draw them.
  NAIP stays the delineator.
- Statistics have to be computed per orbit track, because each track sees
  the ground at a different angle.
- Sentinel-1B was lost in December 2021, so 2022–2024 has a 12-day repeat
  per track; Sentinel-1C (launched December 2024) restores 6 days.

## Proposed experiment (about an hour)

1. In ArcGIS Pro, add the two image services above to a map of a valley with
   both cottonwoods and hay fields, e.g. the Rio Ruidoso/Hondo below
   Ruidoso Downs, over the NAIP riparian band.
2. Digitise about 20 riparian polygons and about 20 irrigated fields.
3. Sample a year of VH and VV for each (*Get Samples*, or a short script).
   Compute three numbers per polygon: median VH, median VH/VV, and VH's
   coefficient of variation through the year.
4. **Decide:** if the two classes separate on CV(VH) or VH/VV with little
   overlap, build `sar_tiles` on the existing tile and mosaic code
   (`mosaic.py`, as for NAIP) and add a "field-like" flag to the riparian
   band. If they overlap, stop: the answer is no, for an hour's work.

## Decision needed from Sam

Building `sar_tiles` needs one of these for geolens to download OPERA
granules:

- a free NASA Earthdata login (recommended; the token goes in
  `~/.netrc`, never in the code), or
- a Planetary Computer account, for `sentinel-1-rtc` at 10 m.

The quick look in step 1 needs neither.

## Sources

- [OPERA RTC-S1 validated product (data.nasa.gov)](https://data.nasa.gov/dataset/opera-radiometric-terrain-corrected-sar-backscatter-from-sentinel-1-validated-product-vers-f3131)
- [OPERA L2 RTC-S1 VH image service (NASA Earthdata GIS)](https://gis.earthdata.nasa.gov/image/rest/services/OPERA_L2_RTC_S1_V1/OPERA_L2_RTC_S1_V1_VH/ImageServer)
- Planetary Computer STAC collection `sentinel-1-rtc` (`msft:requires_account: true`), checked earlier in this session.
