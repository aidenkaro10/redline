/**
 * MOCK job sites: real Florida power plants, typed in by hand.
 *
 * Names and operators are real. Coordinates are APPROXIMATE (good to a few
 * miles) and capacities are rounded. The real list comes from EIA-860M via
 * session C1's plants.geojson. Once that exists, the backend serves it at
 * GET /api/sites and this file is only used by the mock server.
 */

import type { Site } from "./contract.ts";

type Row = [id: string, name: string, operator: string, tech: Site["tech"], mw: number, lat: number, lon: number, county: string, coastal: boolean];

const ROWS: Row[] = [
  ["fl-deerhaven", "Deerhaven Generating Station", "GRU", "gas", 450, 29.7585, -82.3889, "Alachua", false],
  ["fl-kelly", "J.R. Kelly Generating Station", "GRU", "gas", 130, 29.6455, -82.3211, "Alachua", false],
  ["fl-grec", "Deerhaven Renewable (biomass)", "GRU", "biomass", 102, 29.7545, -82.3925, "Alachua", false],
  ["fl-crystal-river", "Crystal River Energy Complex", "Duke Energy", "coal", 3000, 28.959, -82.699, "Citrus", true],
  ["fl-turkey-point", "Turkey Point", "FPL", "nuclear", 3800, 25.4356, -80.3308, "Miami-Dade", true],
  ["fl-st-lucie", "St. Lucie Nuclear Plant", "FPL", "nuclear", 2000, 27.3486, -80.2464, "St. Lucie", true],
  ["fl-big-bend", "Big Bend Power Station", "Tampa Electric", "gas", 1700, 27.7944, -82.4036, "Hillsborough", true],
  ["fl-bayside", "Bayside Power Station", "Tampa Electric", "gas", 1850, 27.907, -82.423, "Hillsborough", true],
  ["fl-polk", "Polk Power Station", "Tampa Electric", "gas", 1200, 27.729, -81.99, "Polk", false],
  ["fl-manatee", "Manatee Energy Center", "FPL", "gas", 1600, 27.606, -82.345, "Manatee", false],
  ["fl-martin", "Martin Clean Energy Center", "FPL", "gas", 3700, 27.053, -80.563, "Martin", false],
  ["fl-fort-myers", "Fort Myers Clean Energy Center", "FPL", "gas", 2700, 26.697, -81.783, "Lee", false],
  ["fl-riviera", "Riviera Beach Clean Energy Center", "FPL", "gas", 1300, 26.765, -80.053, "Palm Beach", true],
  ["fl-port-everglades", "Port Everglades Clean Energy Center", "FPL", "gas", 1250, 26.085, -80.125, "Broward", true],
  ["fl-cape-canaveral", "Cape Canaveral Clean Energy Center", "FPL", "gas", 1300, 28.469, -80.765, "Brevard", true],
  ["fl-sanford", "Sanford Plant", "FPL", "gas", 2000, 28.842, -81.326, "Volusia", false],
  ["fl-lauderdale", "Lauderdale Plant", "FPL", "gas", 900, 26.068, -80.2, "Broward", false],
  ["fl-west-county", "West County Energy Center", "FPL", "gas", 3700, 26.698, -80.374, "Palm Beach", false],
  ["fl-okeechobee", "Okeechobee Clean Energy Center", "FPL", "gas", 1750, 27.38, -80.75, "Okeechobee", false],
  ["fl-dania-beach", "Dania Beach Clean Energy Center", "FPL", "gas", 1200, 26.065, -80.162, "Broward", true],
  ["fl-crist", "Plant Crist", "FPL (Gulf)", "gas", 900, 30.566, -87.226, "Escambia", false],
  ["fl-lansing-smith", "Lansing Smith Plant", "FPL (Gulf)", "gas", 1100, 30.269, -85.7, "Bay", true],
  ["fl-northside", "Northside Generating Station", "JEA", "coal", 1300, 30.43, -81.552, "Duval", true],
  ["fl-brandy-branch", "Brandy Branch Generating Station", "JEA", "gas", 800, 30.32, -81.945, "Duval", false],
  ["fl-stanton", "Stanton Energy Center", "OUC", "coal", 1800, 28.485, -81.17, "Orange", false],
  ["fl-indian-river", "Indian River Plant", "OUC", "gas", 600, 28.493, -80.782, "Brevard", true],
  ["fl-anclote", "Anclote Power Plant", "Duke Energy", "gas", 1000, 28.184, -82.788, "Pasco", true],
  ["fl-bartow", "Bartow Power Plant", "Duke Energy", "gas", 1200, 27.861, -82.602, "Pinellas", true],
  ["fl-hines", "Hines Energy Complex", "Duke Energy", "gas", 2000, 27.788, -81.87, "Polk", false],
  ["fl-osprey", "Osprey Energy Center", "Duke Energy", "gas", 600, 28.043, -81.829, "Polk", false],
  ["fl-tiger-bay", "Tiger Bay Plant", "Duke Energy", "gas", 220, 27.75, -81.86, "Polk", false],
  ["fl-debary", "DeBary Plant", "Duke Energy", "gas", 650, 28.9, -81.33, "Volusia", false],
  ["fl-suwannee", "Suwannee River Plant", "Duke Energy", "gas", 160, 30.377, -83.18, "Suwannee", false],
  ["fl-seminole", "Seminole Generating Station", "Seminole Electric", "coal", 1300, 29.733, -81.633, "Putnam", false],
  ["fl-mcintosh", "McIntosh Power Plant", "Lakeland Electric", "gas", 900, 28.08, -81.925, "Polk", false],
  ["fl-cane-island", "Cane Island Power Park", "KUA / FMPA", "gas", 700, 28.277, -81.539, "Osceola", false],
  ["fl-purdom", "Purdom Generating Station", "City of Tallahassee", "gas", 250, 30.16, -84.195, "Wakulla", true],
  ["fl-hopkins", "Hopkins Generating Station", "City of Tallahassee", "gas", 600, 30.451, -84.4, "Leon", false],
  ["fl-babcock", "Babcock Ranch Solar Energy Center", "FPL", "solar", 75, 26.86, -81.73, "Charlotte", false],
  ["fl-desoto-solar", "DeSoto Next Generation Solar", "FPL", "solar", 25, 27.335, -81.84, "DeSoto", false],
  ["fl-space-coast", "Space Coast Next Generation Solar", "FPL", "solar", 10, 28.482, -80.675, "Brevard", true],
  ["fl-citrus-solar", "Citrus Solar Energy Center", "FPL", "solar", 75, 27.3, -81.97, "DeSoto", false],
  ["fl-manatee-solar", "Manatee Solar Energy Center", "FPL", "solar", 75, 27.62, -82.32, "Manatee", false],
  ["fl-barefoot-bay", "Barefoot Bay Solar Energy Center", "FPL", "solar", 75, 27.87, -80.55, "Brevard", false],
  ["fl-blue-cypress", "Blue Cypress Solar Energy Center", "FPL", "solar", 75, 27.7, -80.6, "Indian River", false],
  ["fl-loggerhead", "Loggerhead Solar Energy Center", "FPL", "solar", 75, 27.42, -80.48, "St. Lucie", false],
  ["fl-coral-farms", "Coral Farms Solar Energy Center", "FPL", "solar", 75, 29.55, -81.68, "Putnam", false],
  ["fl-horizon", "Horizon Solar Energy Center", "FPL", "solar", 75, 29.37, -81.5, "Putnam", false],
  ["fl-hammock", "Hammock Solar Energy Center", "FPL", "solar", 75, 26.75, -81.1, "Hendry", false],
  ["fl-wildflower", "Wildflower Solar Energy Center", "FPL", "solar", 75, 27.22, -81.65, "DeSoto", false],
  ["fl-interstate", "Interstate Solar Energy Center", "FPL", "solar", 75, 27.46, -80.45, "St. Lucie", false],
  ["fl-miami-dade-solar", "Miami-Dade Solar Energy Center", "FPL", "solar", 75, 25.52, -80.47, "Miami-Dade", false],
  ["fl-sunshine-gateway", "Sunshine Gateway Solar Energy Center", "FPL", "solar", 75, 30.27, -82.6, "Columbia", false],
  ["fl-twin-lakes", "Twin Lakes Solar Energy Center", "FPL", "solar", 75, 29.68, -81.8, "Putnam", false],
  ["fl-echo-river", "Echo River Solar Energy Center", "FPL", "solar", 75, 30.32, -82.95, "Suwannee", false],
  ["fl-hibiscus", "Hibiscus Solar Energy Center", "FPL", "solar", 75, 26.73, -80.4, "Palm Beach", false],
  ["fl-pioneer-trail", "Pioneer Trail Solar Energy Center", "FPL", "solar", 75, 29.03, -81.13, "Volusia", false],
  ["fl-lake-placid", "Lake Placid Solar", "Duke Energy", "solar", 45, 27.29, -81.36, "Highlands", false],
  ["fl-columbia-solar", "Columbia Solar", "Duke Energy", "solar", 75, 30.1, -82.7, "Columbia", false],
  ["fl-hamilton-solar", "Hamilton Solar", "Duke Energy", "solar", 75, 30.44, -83.0, "Hamilton", false],
];

export const SITES: Site[] = ROWS.map(([id, name, operator, tech, capacity_mw, lat, lon, county, coastal]) => ({
  id, name, operator, tech, capacity_mw, lat, lon, county, coastal,
}));

export const SITE_BY_ID: Record<string, Site> = Object.fromEntries(SITES.map((s) => [s.id, s]));
