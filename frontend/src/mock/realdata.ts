/**
 * Real outside data for the built-in demo backend (server side only, free, no API keys):
 *
 *   Open-Meteo   current temperature + humidity for every site, and the hourly forecast for
 *                today and tomorrow (heat index peaks, thunderstorm chance). One request per
 *                50 sites, refreshed every 15 minutes.
 *   iNaturalist  research-grade sightings of dangerous animals (alligators, venomous snakes)
 *                near the crew sites in the last 30 days, with the real map point.
 *
 * Heat index uses the NWS formula (Rothfusz + adjustments). WBGT is an estimate in the shade
 * (0.7 x wet-bulb + 0.3 x air temperature, wet-bulb from Stull 2011), because a true WBGT needs a
 * black-globe sensor. In direct sun the real WBGT runs a few degrees higher.
 * If a request fails, the engine keeps the last good data (or its sample values).
 */

export interface SiteWeather {
  tempF: number;
  rh: number;                 // %
  windMph: number;
  hiNowF: number;             // NWS heat index now
  wbgtC: number;              // shade WBGT estimate
  hiMaxTodayF: number;        // highest heat index today (whole day, forecast + observed)
  hiMaxTomorrowF: number;
  stormChanceTomorrow: number;   // % chance of rain tomorrow (max hourly)
  thunderTomorrow: boolean;      // a thunderstorm weather code in tomorrow's forecast
  at: number;                 // Unix seconds of the reading
}

export interface Sighting {
  name: string;               // scientific name
  common: string;             // "American Alligator"
  lat: number;
  lon: number;
  miles: number;              // from the site
  observed_on: string;        // "2026-10-12"
  url: string;
  approximate: boolean;       // iNaturalist blurred the location (privacy or sensitive species)
}

// ---------------------------------------------------------------------------
// Formulas
// ---------------------------------------------------------------------------

/** NWS heat index in °F (https://www.wpc.ncep.noaa.gov/html/heatindex_equation.shtml). */
export function heatIndexF(tF: number, rh: number): number {
  const simple = 0.5 * (tF + 61 + (tF - 68) * 1.2 + rh * 0.094);
  if ((simple + tF) / 2 < 80) return simple;
  let hi = -42.379 + 2.04901523 * tF + 10.14333127 * rh - 0.22475541 * tF * rh - 0.00683783 * tF * tF
    - 0.05481717 * rh * rh + 0.00122874 * tF * tF * rh + 0.00085282 * tF * rh * rh - 0.00000199 * tF * tF * rh * rh;
  if (rh < 13 && tF >= 80 && tF <= 112) hi -= ((13 - rh) / 4) * Math.sqrt((17 - Math.abs(tF - 95)) / 17);
  else if (rh > 85 && tF >= 80 && tF <= 87) hi += ((rh - 85) / 10) * ((87 - tF) / 5);
  return hi;
}

/** Wet-bulb temperature, °C, from air temperature and humidity (Stull 2011, good for 5-99% RH). */
export function wetBulbC(tC: number, rh: number): number {
  return tC * Math.atan(0.151977 * Math.sqrt(rh + 8.313659)) + Math.atan(tC + rh) - Math.atan(rh - 1.676331)
    + 0.00391838 * rh ** 1.5 * Math.atan(0.023101 * rh) - 4.686035;
}

/** WBGT estimate in the shade, °C: 0.7 x wet-bulb + 0.3 x air temperature (no sun term). */
export function wbgtShadeC(tC: number, rh: number): number {
  return 0.7 * wetBulbC(tC, rh) + 0.3 * tC;
}

/** Great-circle distance in miles. */
export function milesBetween(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = (d: number) => (d * Math.PI) / 180;
  const a = Math.sin(r(lat2 - lat1) / 2) ** 2 + Math.cos(r(lat1)) * Math.cos(r(lat2)) * Math.sin(r(lon2 - lon1) / 2) ** 2;
  return 3958.8 * 2 * Math.asin(Math.sqrt(a));
}

const fToC = (f: number) => ((f - 32) * 5) / 9;
const round1 = (x: number) => Math.round(x * 10) / 10;

// ---------------------------------------------------------------------------
// Open-Meteo
// ---------------------------------------------------------------------------

type OpenMeteo = {
  utc_offset_seconds: number;
  current: { time: string; temperature_2m: number; relative_humidity_2m: number; wind_speed_10m: number };
  hourly: { time: string[]; temperature_2m: number[]; relative_humidity_2m: number[]; precipitation_probability: (number | null)[]; weather_code: (number | null)[] };
};

/** Weather for many sites, keyed by site id. Sites that fail are simply missing. */
export async function fetchWeather(sites: { id: string; lat: number; lon: number }[]): Promise<Record<string, SiteWeather>> {
  const out: Record<string, SiteWeather> = {};
  for (let i = 0; i < sites.length; i += 50) {
    const batch = sites.slice(i, i + 50);
    const url = "https://api.open-meteo.com/v1/forecast?" + new URLSearchParams({
      latitude: batch.map((s) => s.lat.toFixed(4)).join(","),
      longitude: batch.map((s) => s.lon.toFixed(4)).join(","),
      current: "temperature_2m,relative_humidity_2m,wind_speed_10m",
      hourly: "temperature_2m,relative_humidity_2m,precipitation_probability,weather_code",
      forecast_days: "2", temperature_unit: "fahrenheit", wind_speed_unit: "mph", timezone: "America/New_York",
    });
    const res = await fetch(url, { signal: AbortSignal.timeout(15000) });
    if (!res.ok) throw new Error(`Open-Meteo ${res.status}`);
    const json = await res.json() as OpenMeteo | OpenMeteo[];
    const list = Array.isArray(json) ? json : [json];
    list.forEach((d, k) => {
      const s = batch[k];
      if (!s || !d?.current || !d?.hourly) return;
      const c = d.current;
      const his = d.hourly.temperature_2m.map((t, h) => heatIndexF(t, d.hourly.relative_humidity_2m[h]));
      const today = his.slice(0, 24), tomorrow = his.slice(24, 48);
      const rainTomorrow = d.hourly.precipitation_probability.slice(24, 48).map((p) => p ?? 0);
      const codesTomorrow = d.hourly.weather_code.slice(24, 48).map((x) => x ?? 0);
      out[s.id] = {
        tempF: round1(c.temperature_2m), rh: Math.round(c.relative_humidity_2m), windMph: round1(c.wind_speed_10m),
        hiNowF: Math.round(heatIndexF(c.temperature_2m, c.relative_humidity_2m)),
        wbgtC: round1(wbgtShadeC(fToC(c.temperature_2m), c.relative_humidity_2m)),
        hiMaxTodayF: Math.round(Math.max(...today)), hiMaxTomorrowF: Math.round(Math.max(...tomorrow)),
        stormChanceTomorrow: Math.round(Math.max(0, ...rainTomorrow)),
        thunderTomorrow: codesTomorrow.some((x) => x >= 95),
        at: Math.round(Date.parse(c.time + ":00Z") / 1000 - (d.utc_offset_seconds ?? 0)) || Math.round(Date.now() / 1000),
      };
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// iNaturalist
// ---------------------------------------------------------------------------

/** Genera that matter on a job site: alligators and Florida's venomous snakes. */
const DANGEROUS = ["Alligator", "Crotalus", "Sistrurus", "Agkistrodon", "Micrurus"];

type INatObs = {
  observed_on: string; uri: string; obscured: boolean; geojson: { coordinates: [number, number] } | null;
  taxon: { name: string; preferred_common_name?: string } | null;
};

/**
 * The most useful dangerous-animal sighting within ~6 miles in the last 30 days: the nearest one
 * with an exact location, or else the newest blurred one. null = nothing reported.
 */
export async function fetchSighting(site: { lat: number; lon: number }, now = Date.now()): Promise<Sighting | null> {
  const d1 = new Date(now - 30 * 86400000).toISOString().slice(0, 10);
  const url = "https://api.inaturalist.org/v1/observations?" + new URLSearchParams({
    lat: String(site.lat), lng: String(site.lon), radius: "10", iconic_taxa: "Reptilia",
    quality_grade: "research", d1, order_by: "observed_on", per_page: "100",
  });
  const res = await fetch(url, { signal: AbortSignal.timeout(15000), headers: { "User-Agent": "Redline heat-safety demo (SwampHacks)" } });
  if (!res.ok) throw new Error(`iNaturalist ${res.status}`);
  const json = await res.json() as { results: INatObs[] };
  const hits = (json.results ?? [])
    .filter((o) => o.taxon && o.geojson && DANGEROUS.some((g) => o.taxon!.name.startsWith(g)))
    .map((o): Sighting => {
      const [lon, lat] = o.geojson!.coordinates;
      return {
        name: o.taxon!.name, common: o.taxon!.preferred_common_name ?? o.taxon!.name, lat, lon,
        miles: round1(milesBetween(site.lat, site.lon, lat, lon)), observed_on: o.observed_on, url: o.uri, approximate: !!o.obscured,
      };
    });
  const exact = hits.filter((h) => !h.approximate).sort((a, b) => a.miles - b.miles)[0];
  return exact ?? hits[0] ?? null;
}
