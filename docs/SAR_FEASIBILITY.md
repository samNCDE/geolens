# Sentinel-1 SAR in geolens: what is possible

*Written 15 Oct 2026; updated 16 Oct for v0.31.0, which builds the
statistics the experiment below needs (Sam has an Earthdata login). Whether
radar earns a place in the riparian band is still the experiment's call.*

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

## The experiment (about an hour, with v0.31)

1. Pick a valley with both cottonwoods and hay fields, e.g. the Rio
   Ruidoso/Hondo below Ruidoso Downs. Map its riparian band (step 6).
2. Digitise about 20 irrigated fields into a polygon layer, and merge it
   with about 20 riparian polygons from the band. Add a `kind` field:
   riparian or field.
3. Run *0 Get data ▸ Sentinel-1 radar statistics (OPERA RTC)* over the
   valley for a year, with those polygons. (First, once: *0 Setup ▸ Save
   Earthdata login*.)
4. In *Radar statistics per polygon*, compare the two kinds on `sar_vh_cv`
   (how much VH varies) and `sar_vh_vv_db`. A scatter chart of the two,
   coloured by `kind`, shows it at a glance.
5. **Decide:**
   - If the kinds separate with little overlap, the next build is a
     "field-like" flag on the riparian band, with the threshold read off
     this chart.
   - If they overlap, stop: radar does not help here, and it cost an hour.

The NASA image services above remain a no-login way to look at single
dates.

## How it is built (v0.31.0)

- **Login.** Each person saves their own free login once (*0 Setup ▸ Save
  Earthdata login*, or `geolens.sar.save_login`). It goes into the netrc
  file in their Windows profile (`%USERPROFILE%\_netrc`), which NASA's
  tools, curl and GDAL read. It is never in geolens, its run records, or a
  shared file.
- **Finding passes.** NASA's CMR catalog for collection `C2777436413-ASF`
  (OPERA_L2_RTC-S1_V1), with no login needed. A burst processed twice is
  kept in its newest version only.
- **Reading.** Each burst's VV, VH and mask are read onto the area's 30 m
  UTM grid through GDAL. If GDAL cannot get through (a TLS-inspecting
  proxy), the files are downloaded whole with Python's HTTPS, as for NAIP.
- **Tiles.** Pixels in shadow or layover, or flagged invalid in OPERA's
  mask, are dropped. One VV/VH tile is written per pass, and reused on a
  rerun.
- **Statistics.** Computed per track, then combined, weighted by passes.
  Optionally summarised per polygon.
- **Tested offline only,** against stand-in bursts (`tests/test_sar.py`).
  The URL patterns and the login flow follow ASF's and NASA's
  documentation but have not met the real servers yet.

## Sources

- [OPERA RTC-S1 validated product (data.nasa.gov)](https://data.nasa.gov/dataset/opera-radiometric-terrain-corrected-sar-backscatter-from-sentinel-1-validated-product-vers-f3131)
- [OPERA L2 RTC-S1 VH image service (NASA Earthdata GIS)](https://gis.earthdata.nasa.gov/image/rest/services/OPERA_L2_RTC_S1_V1/OPERA_L2_RTC_S1_V1_VH/ImageServer)
- Planetary Computer STAC collection `sentinel-1-rtc` (`msft:requires_account: true`), checked 15 Oct 2026.
- [OPERA RTC for Sentinel-1 (RTC-S1) Product Guide (ASF)](https://hyp3-docs.asf.alaska.edu/guides/opera_rtc_product_guide/): file naming, layers, mask values.
- [OPERA RTC-S1 on the Registry of Open Data on AWS](https://registry.opendata.aws/nasa-operal2rtc-s1v1/)
